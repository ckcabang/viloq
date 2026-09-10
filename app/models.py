"""Persisted records, mapped to tables.

These are the shapes the database holds. They are deliberately separate from
the wire schemas in `app/schemas.py`: changing storage should not touch the
HTTP contract, and vice versa.

Nothing here is dialect-specific. Column types are the portable SQLAlchemy
ones, and the two custom types below exist precisely so that SQLite and, later,
Postgres hand the rest of the app identical Python values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC
from datetime import date as Date
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator):
    """A `datetime` that is always tz-aware UTC, whatever the backend.

    Postgres round-trips the time zone; SQLite has no time zone type and hands
    back naive values. Normalising on the way in and out keeps comparisons like
    `now() > link.expires_at` from raising on one backend and not the other.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass
class SplitInput:
    """What the client asked for on one participant (percent, share, exact…)."""

    member_id: str
    raw: float | None = None


@dataclass
class ExpenseShare:
    """What the server allocated to one participant, in minor units."""

    member_id: str
    amount_minor: int


class _DataclassList(TypeDecorator):
    """A short, always-replaced list of small records, held in one JSON column.

    Splits are only ever read or rewritten whole, alongside their expense, so a
    child table would buy nothing but joins. `JSON` is generic SQLAlchemy: TEXT
    on SQLite, `json` on Postgres (worth a `JSONB` variant if it ever needs
    indexing).
    """

    impl = JSON
    cache_ok = True
    record: type

    def process_bind_param(self, value, dialect) -> list[dict] | None:
        if value is None:
            return None
        return [asdict(item) for item in value]

    def process_result_value(self, value, dialect) -> list:
        if value is None:
            return []
        return [self.record(**item) for item in value]


class SplitInputList(_DataclassList):
    record = SplitInput


class ExpenseShareList(_DataclassList):
    record = ExpenseShare


class Base(DeclarativeBase):
    # Annotation -> column type, so every `Mapped[datetime]` below is UTC-safe
    # and the split lists map themselves.
    type_annotation_map = {
        datetime: UTCDateTime,
        list[SplitInput]: SplitInputList,
        list[ExpenseShare]: ExpenseShareList,
    }


# Ids are `<prefix>_<16 hex chars>`; tokens are hex of at most 48 chars.
Id = String(64)
Token = String(128)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Id, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(Token, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime]


class MagicLink(Base):
    __tablename__ = "magic_links"

    token: Mapped[str] = mapped_column(Token, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    expires_at: Mapped[datetime]
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(Id, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    invite_code: Mapped[str] = mapped_column(String(32), unique=True)
    invite_token: Mapped[str] = mapped_column(Token)
    invite_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    version: Mapped[int] = mapped_column(Integer, default=1)


class Member(Base):
    """One person's membership of one group. Display names are per group."""

    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_member"),)

    id: Mapped[str] = mapped_column(Id, primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[str] = mapped_column(Id, primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    description: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    amount_minor: Mapped[int] = mapped_column(Integer)
    date: Mapped[Date]
    payer_member_id: Mapped[str] = mapped_column(Id)
    split_type: Mapped[str] = mapped_column(String(20))
    split_inputs: Mapped[list[SplitInput]]
    shares: Mapped[list[ExpenseShare]]
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    version: Mapped[int] = mapped_column(Integer, default=1)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(Id, primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), index=True)
    payer_member_id: Mapped[str] = mapped_column(Id)
    recipient_member_id: Mapped[str] = mapped_column(Id)
    amount_minor: Mapped[int] = mapped_column(Integer)
    date: Mapped[Date]
    note: Mapped[str] = mapped_column(Text, default="")
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    version: Mapped[int] = mapped_column(Integer, default=1)
