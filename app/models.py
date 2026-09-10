"""Internal storage records.

These are the shapes the (mock) database holds. They are deliberately separate
from the wire schemas in `app/schemas.py`: swapping the mock store for a real
database should not touch the HTTP contract, and vice versa.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime


@dataclass
class User:
    id: str
    email: str
    display_name: str
    email_verified: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class Session:
    token: str
    user_id: str
    created_at: datetime


@dataclass
class MagicLink:
    token: str
    email: str
    expires_at: datetime
    used: bool = False


@dataclass
class Group:
    id: str
    name: str
    currency: str
    created_by_user_id: str
    invite_code: str
    invite_token: str
    invite_revoked: bool
    created_at: datetime
    updated_at: datetime
    version: int = 1


@dataclass
class Member:
    id: str
    group_id: str
    user_id: str
    display_name: str
    created_at: datetime
    updated_at: datetime


@dataclass
class SplitInput:
    member_id: str
    raw: float | None = None


@dataclass
class ExpenseShare:
    member_id: str
    amount_minor: int


@dataclass
class Expense:
    id: str
    group_id: str
    description: str
    note: str
    amount_minor: int
    date: Date
    payer_member_id: str
    split_type: str
    split_inputs: list[SplitInput]
    shares: list[ExpenseShare]
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime
    version: int = 1


@dataclass
class Payment:
    id: str
    group_id: str
    payer_member_id: str
    recipient_member_id: str
    amount_minor: int
    date: Date
    note: str
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime
    version: int = 1
