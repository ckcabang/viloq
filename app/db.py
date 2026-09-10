"""Persistence: the engine, and the repository the routers talk to.

Everything the routers do goes through `Database`, so the rest of the app never
sees a session, a query, or a dialect. Which database that is comes from
`VILOQ_DATABASE_URL` (see `app.config.database_url`) — SQLite by default, and
nothing here is written against it. The only dialect-aware code is
`_sqlite_options` / `_configure_sqlite`, deliberately fenced off so that adding
Postgres later means installing a driver and setting the URL.

Concurrency: one `Database`, holding one session, per request. Read-modify-write
sequences are wrapped in `transaction()`, which on SQLite takes the write lock
up front (see `_configure_sqlite`), so two requests editing the same record
cannot interleave. A write outside such a block commits on its own.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session as SASession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql import Select

from app.config import database_url
from app.models import (
    Base,
    Expense,
    Group,
    MagicLink,
    Member,
    Payment,
    Session,
    User,
)

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


def _sqlite_options(url) -> dict:
    """Connection settings SQLite needs and other backends do not."""
    options: dict = {
        # Sync endpoints run in a threadpool, so connections cross threads; and
        # a writer should wait for the lock rather than fail instantly.
        "connect_args": {"check_same_thread": False, "timeout": 30},
    }
    if url.database in (None, "", ":memory:"):
        # Every connection to an in-memory database gets its own empty one.
        # Pinning a single connection is what makes such a URL behave.
        options["poolclass"] = StaticPool
    return options


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record) -> None:
        # Hand transaction control to SQLAlchemy, so `_on_begin` below is the
        # only thing that opens one.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection) -> None:
        # A plain SQLite BEGIN defers the write lock until the first write, so
        # a read-modify-write can read a row that another transaction is about
        # to change, then fail at commit. IMMEDIATE takes the lock at the start
        # instead — the atomicity `transaction()` advertises. Other backends
        # give the same guarantee through MVCC.
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def create_db_engine(url: str | None = None) -> Engine:
    """An engine for `url` (default: the configured one), tuned per dialect."""
    parsed = make_url(url or database_url())
    options: dict = {"pool_pre_ping": True}
    if parsed.get_backend_name() == "sqlite":
        options |= _sqlite_options(parsed)

    engine = create_engine(parsed, **options)
    if engine.dialect.name == "sqlite":
        _configure_sqlite(engine)
    return engine


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


@contextmanager
def in_memory_database() -> Iterator[Database]:
    """A private, throwaway SQLite database. For tests and scratch work."""
    scratch = create_db_engine("sqlite+pysqlite://")
    Base.metadata.create_all(scratch)
    try:
        with new_session_factory(scratch)() as session:
            yield Database(session)
    finally:
        scratch.dispose()


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

    def _first(self, statement: Select):
        return self._session.execute(statement).scalars().first()

    def _all(self, statement: Select) -> list:
        return list(self._session.execute(statement).scalars())

    # ---- Auth ------------------------------------------------------------

    def create_magic_link(self, email: str) -> MagicLink:
        link = MagicLink(
            token=high_entropy_token(18),
            email=email,
            expires_at=now() + timedelta(minutes=MAGIC_LINK_TTL_MINUTES),
            used=False,
        )
        self._save(link)
        return link

    def magic_link(self, token: str) -> MagicLink | None:
        return self._session.get(MagicLink, token)

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
        record = self._session.get(Session, token)
        return self._session.get(User, record.user_id) if record else None

    def delete_session(self, token: str | None) -> None:
        self._delete(self._session.get(Session, token) if token else None)

    # ---- Groups and members ---------------------------------------------

    def group(self, group_id: str) -> Group | None:
        return self._session.get(Group, group_id)

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
        return self._session.get(Expense, expense_id)

    def payment(self, payment_id: str) -> Payment | None:
        return self._session.get(Payment, payment_id)

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
