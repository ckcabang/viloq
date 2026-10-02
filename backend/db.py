"""Persistence: the engine, and the repository the routers talk to.

Everything the routers do goes through `Database`, so the rest of the app never
sees a session, a query, or a dialect. Which database that is comes from
`VILOQ_DATABASE_URL` (see `backend.config.database_url`): a Postgres database.

Concurrency: one `Database`, holding one session, per request. Read-modify-write
sequences are wrapped in `transaction()`, and every read inside one locks the
rows it returns (`SELECT ... FOR UPDATE`). Postgres's default READ COMMITTED
would otherwise let two requests both read version 1 of a record, both pass the
`If-Match` check, and both write — a lost update. With the lock, the second
request waits for the first to commit, then reads what it wrote, so it sees the
new version (409) or no row at all (404). Every mutation reaches its group row
first, so locks are always taken in the same order and cannot deadlock. A write
outside such a block commits on its own.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session as SASession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import Select

from backend.config import database_url
from backend.models import (
    Base,
    Expense,
    Group,
    MagicLink,
    Member,
    Payment,
    Session,
    User,
)

T = TypeVar("T")

MAGIC_LINK_TTL_MINUTES = 15

# Ambiguous glyphs (I, L, O, 0, 1) are left out so codes can be read aloud.
_INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def now() -> datetime:
    return datetime.now(UTC)


def random_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def high_entropy_token(nbytes: int = 24) -> str:
    return secrets.token_hex(nbytes)


def invite_code() -> str:
    raw = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(9))
    return f"{raw[0:3]}-{raw[3:6]}-{raw[6:9]}"


# ---- Engine --------------------------------------------------------------


def create_db_engine(url: str | None = None) -> Engine:
    """An engine for `url` (default: the configured one)."""
    return create_engine(url or database_url(), pool_pre_ping=True)


def new_session_factory(bound: Engine) -> sessionmaker[SASession]:
    """Sessions whose loaded values stay readable after commit.

    Endpoints build their response from records they already hold, sometimes
    after the transaction has closed; expiring on commit would send them back
    to the database for values that cannot have changed.
    """
    return sessionmaker(bind=bound, expire_on_commit=False)


_engine: Engine | None = None
_sessions: sessionmaker[SASession] | None = None


def engine() -> Engine:
    """The process-wide engine, built on first use."""
    global _engine, _sessions
    if _engine is None:
        _engine = create_db_engine()
        _sessions = new_session_factory(_engine)
    return _engine


def init_db() -> None:
    """Create any missing tables.

    Enough while the schema only ever grows; one that changes shape will want
    migrations instead.
    """
    Base.metadata.create_all(engine())


def get_db() -> Iterator[Database]:
    """FastAPI dependency: one session, and one `Database`, per request."""
    engine()  # builds `_sessions` on the first request
    assert _sessions is not None
    with _sessions() as session:
        yield Database(session)


# ---- Repository ----------------------------------------------------------


class Database:
    """The queries and mutations the routers need, over one session."""

    def __init__(self, session: SASession) -> None:
        self._session = session
        self._depth = 0

    @property
    def session(self) -> SASession:
        """The session itself, for anything this surface does not cover."""
        return self._session

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run a read-modify-write as one atomic unit.

        Commits on the way out, rolls back if the body raises — including the
        `ApiError`s routers raise mid-sequence, so a rejected request leaves
        nothing behind. Nesting is allowed; only the outermost block commits.

        Records edited in place inside the block are saved with it, so an edit
        belongs in one whether or not it also adds anything.
        """
        self._depth += 1
        try:
            yield
        except BaseException:
            if self._depth == 1:
                self._session.rollback()
            raise
        else:
            if self._depth == 1:
                self._session.commit()
        finally:
            self._depth -= 1

    def ping(self) -> None:
        """Round-trip to the server; raises if it cannot be reached."""
        self._session.execute(text("SELECT 1"))

    def _save(self, *records: object) -> None:
        """Persist new records. Edits to existing ones ride on the commit."""
        self._session.add_all(records)
        self._settle()

    def _settle(self) -> None:
        # Inside a `transaction()` the outermost block commits; a lone write
        # commits itself, so callers never have to think about which they are.
        if self._depth:
            self._session.flush()
        else:
            self._session.commit()

    def _delete(self, record: object | None) -> None:
        if record is not None:
            self._session.delete(record)
            self._settle()

    def _locking(self, statement: Select) -> Select:
        # Inside a `transaction()`, lock what is read until the commit, and
        # refresh any copy this session already holds: the row may have changed
        # while the lock was being waited for.
        if not self._depth:
            return statement
        return statement.with_for_update().execution_options(populate_existing=True)

    def _get(self, model: type[T], key: str) -> T | None:
        if not self._depth:
            return self._session.get(model, key)
        return self._session.get(
            model, key, with_for_update=True, populate_existing=True
        )

    def _first(self, statement: Select):
        return self._session.execute(self._locking(statement)).scalars().first()

    def _all(self, statement: Select) -> list:
        return list(self._session.execute(self._locking(statement)).scalars())

    # ---- Auth ------------------------------------------------------------

    def create_magic_link(
        self, email: str, ttl_minutes: int = MAGIC_LINK_TTL_MINUTES
    ) -> MagicLink:
        link = MagicLink(
            token=high_entropy_token(18),
            email=email,
            expires_at=now() + timedelta(minutes=ttl_minutes),
            used=False,
        )
        self._save(link)
        return link

    def magic_link(self, token: str) -> MagicLink | None:
        return self._get(MagicLink, token)

    def user_by_email(self, email: str) -> User | None:
        return self._first(select(User).where(User.email == email))

    def create_user(self, email: str, display_name: str = "") -> User:
        stamp = now()
        user = User(
            id=random_id("usr"),
            email=email,
            display_name=display_name,
            email_verified=True,
            created_at=stamp,
            updated_at=stamp,
        )
        self._save(user)
        return user

    def create_session(self, user_id: str) -> Session:
        session = Session(
            token=high_entropy_token(20), user_id=user_id, created_at=now()
        )
        self._save(session)
        return session

    def user_for_session(self, token: str | None) -> User | None:
        if not token:
            return None
        record = self._get(Session, token)
        return self._get(User, record.user_id) if record else None

    def delete_session(self, token: str | None) -> None:
        self._delete(self._get(Session, token) if token else None)

    # ---- Groups and members ---------------------------------------------

    def group(self, group_id: str) -> Group | None:
        return self._get(Group, group_id)

    def unique_invite_code(self) -> str:
        """A fresh code no live group is already using."""
        while True:
            code = invite_code()
            if self.group_by_invite_code(code) is None:
                return code

    def group_by_invite_code(self, code: str) -> Group | None:
        return self._first(select(Group).where(Group.invite_code == code))

    def members_of(self, group_id: str) -> list[Member]:
        return self._all(
            select(Member)
            .where(Member.group_id == group_id)
            .order_by(Member.created_at, Member.id)
        )

    def membership(self, user_id: str, group_id: str) -> Member | None:
        return self._first(
            select(Member).where(
                Member.group_id == group_id, Member.user_id == user_id
            )
        )

    def memberships_of_user(self, user_id: str) -> list[Member]:
        return self._all(
            select(Member)
            .where(Member.user_id == user_id)
            .order_by(Member.created_at, Member.id)
        )

    def add_group(self, group: Group) -> Group:
        self._save(group)
        return group

    def add_member(self, member: Member) -> Member:
        self._save(member)
        return member

    # ---- Transactions ----------------------------------------------------

    def expenses_of(self, group_id: str) -> list[Expense]:
        return self._all(
            select(Expense)
            .where(Expense.group_id == group_id)
            .order_by(Expense.created_at, Expense.id)
        )

    def payments_of(self, group_id: str) -> list[Payment]:
        return self._all(
            select(Payment)
            .where(Payment.group_id == group_id)
            .order_by(Payment.created_at, Payment.id)
        )

    def expense(self, expense_id: str) -> Expense | None:
        return self._get(Expense, expense_id)

    def payment(self, payment_id: str) -> Payment | None:
        return self._get(Payment, payment_id)

    def add_expense(self, expense: Expense) -> Expense:
        self._save(expense)
        return expense

    def delete_expense(self, expense_id: str) -> None:
        self._delete(self.expense(expense_id))

    def add_payment(self, payment: Payment) -> Payment:
        self._save(payment)
        return payment

    def delete_payment(self, payment_id: str) -> None:
        self._delete(self.payment(payment_id))
