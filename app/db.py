"""Mock database.

An in-memory store behind a small repository surface. Everything the routers do
goes through `Database`, so replacing this with a real database later means
reimplementing this one class (and making the methods async) without touching
the routers.

Not safe for multi-process deployment — a single process holds all state, and it
is lost on restart. That is intentional for now.
"""

from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime, timedelta

from app.models import (
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


class Database:
    """In-memory tables plus the queries the routers need."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.users: dict[str, User] = {}
        self.sessions: dict[str, Session] = {}
        self.magic_links: dict[str, MagicLink] = {}
        self.groups: dict[str, Group] = {}
        self.members: dict[str, Member] = {}
        self.expenses: dict[str, Expense] = {}
        self.payments: dict[str, Payment] = {}

    @property
    def lock(self) -> threading.RLock:
        """Held across read-modify-write sequences so mutations stay atomic."""
        return self._lock

    def reset(self) -> None:
        with self._lock:
            self.users.clear()
            self.sessions.clear()
            self.magic_links.clear()
            self.groups.clear()
            self.members.clear()
            self.expenses.clear()
            self.payments.clear()

    # ---- Auth ------------------------------------------------------------

    def create_magic_link(self, email: str) -> MagicLink:
        token = high_entropy_token(18)
        link = MagicLink(
            token=token,
            email=email,
            expires_at=now() + timedelta(minutes=MAGIC_LINK_TTL_MINUTES),
        )
        with self._lock:
            self.magic_links[token] = link
        return link

    def magic_link(self, token: str) -> MagicLink | None:
        return self.magic_links.get(token)

    def user_by_email(self, email: str) -> User | None:
        return next((u for u in self.users.values() if u.email == email), None)

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
        with self._lock:
            self.users[user.id] = user
        return user

    def create_session(self, user_id: str) -> Session:
        session = Session(
            token=high_entropy_token(20), user_id=user_id, created_at=now()
        )
        with self._lock:
            self.sessions[session.token] = session
        return session

    def user_for_session(self, token: str | None) -> User | None:
        if not token:
            return None
        session = self.sessions.get(token)
        return self.users.get(session.user_id) if session else None

    def delete_session(self, token: str | None) -> None:
        if token:
            with self._lock:
                self.sessions.pop(token, None)

    # ---- Groups and members ---------------------------------------------

    def group(self, group_id: str) -> Group | None:
        return self.groups.get(group_id)

    def unique_invite_code(self) -> str:
        """A fresh code no live group is already using."""
        while True:
            code = invite_code()
            if self.group_by_invite_code(code) is None:
                return code

    def group_by_invite_code(self, code: str) -> Group | None:
        return next(
            (g for g in self.groups.values() if g.invite_code == code), None
        )

    def members_of(self, group_id: str) -> list[Member]:
        return [m for m in self.members.values() if m.group_id == group_id]

    def membership(self, user_id: str, group_id: str) -> Member | None:
        return next(
            (
                m
                for m in self.members.values()
                if m.group_id == group_id and m.user_id == user_id
            ),
            None,
        )

    def memberships_of_user(self, user_id: str) -> list[Member]:
        return [m for m in self.members.values() if m.user_id == user_id]

    def add_group(self, group: Group) -> Group:
        with self._lock:
            self.groups[group.id] = group
        return group

    def add_member(self, member: Member) -> Member:
        with self._lock:
            self.members[member.id] = member
        return member

    # ---- Transactions ----------------------------------------------------

    def expenses_of(self, group_id: str) -> list[Expense]:
        return [e for e in self.expenses.values() if e.group_id == group_id]

    def payments_of(self, group_id: str) -> list[Payment]:
        return [p for p in self.payments.values() if p.group_id == group_id]

    def expense(self, expense_id: str) -> Expense | None:
        return self.expenses.get(expense_id)

    def payment(self, payment_id: str) -> Payment | None:
        return self.payments.get(payment_id)

    def add_expense(self, expense: Expense) -> Expense:
        with self._lock:
            self.expenses[expense.id] = expense
        return expense

    def delete_expense(self, expense_id: str) -> None:
        with self._lock:
            self.expenses.pop(expense_id, None)

    def add_payment(self, payment: Payment) -> Payment:
        with self._lock:
            self.payments[payment.id] = payment
        return payment

    def delete_payment(self, payment_id: str) -> None:
        with self._lock:
            self.payments.pop(payment_id, None)


db = Database()


def get_db() -> Database:
    """FastAPI dependency; overridable in tests."""
    return db
