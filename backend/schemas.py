"""Wire schemas — one Pydantic model per schema in `openapi.yaml`.

Field names are camelCase to match the contract exactly, so what FastAPI
generates lines up with the hand-written spec.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CurrencyCode = Literal["USD", "EUR", "GBP", "CAD", "AUD", "INR", "JPY"]
SplitType = Literal["equal", "percentage", "share", "exact"]
Strategy = Literal["minimized", "relationship"]


class Schema(BaseModel):
    """Base for every wire model.

    Unknown fields are ignored rather than rejected, so a client sending a
    field the contract does not define (a stale `payerMemberId` on a payment,
    say) is not an error — the value is simply not read.
    """

    model_config = ConfigDict(extra="ignore")


# ---- Errors ---------------------------------------------------------------


class Error(Schema):
    code: str
    message: str


class VersionConflictError(Error):
    code: Literal["version_conflict"] = "version_conflict"
    current: dict[str, Any]


# ---- Auth -----------------------------------------------------------------


class MagicLinkRequest(Schema):
    email: str


class MagicLinkRequestResult(Schema):
    email: str
    expiresInMinutes: int
    token: str | None = None
    magicLinkPath: str | None = None


class VerifyRequest(Schema):
    token: str


class User(Schema):
    id: str
    email: str
    displayName: str
    emailVerified: bool
    createdAt: datetime
    updatedAt: datetime


class VerifyResult(Schema):
    sessionToken: str
    user: User
    needsDisplayName: bool


class DisplayNameRequest(Schema):
    displayName: str


# ---- Groups ---------------------------------------------------------------


class GroupSummary(Schema):
    id: str
    name: str
    currency: CurrencyCode
    memberCount: int
    expenseCount: int
    myMemberId: str
    myDisplayName: str
    myNetMinor: int


class CreateGroupRequest(Schema):
    name: str
    currency: CurrencyCode
    displayName: str


class UpdateGroupRequest(Schema):
    name: str | None = None
    currency: CurrencyCode | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "UpdateGroupRequest":
        if self.name is None and self.currency is None:
            raise ValueError("Send at least one field to change.")
        return self


class Group(Schema):
    id: str
    name: str
    currency: CurrencyCode
    createdByUserId: str
    inviteCode: str
    inviteRevoked: bool
    createdAt: datetime
    updatedAt: datetime
    version: int


class GroupView(Group):
    isCreator: bool
    currencyLocked: bool
    invitePath: str


# ---- Members --------------------------------------------------------------


class Member(Schema):
    id: str
    groupId: str
    userId: str
    displayName: str
    createdAt: datetime
    updatedAt: datetime


class MemberView(Member):
    isMe: bool


class MeRef(Schema):
    memberId: str
    displayName: str
    userId: str


# ---- Invites --------------------------------------------------------------


class InviteInfo(Schema):
    groupId: str
    groupName: str
    currency: CurrencyCode
    revoked: bool
    memberCount: int


class JoinRequest(Schema):
    displayName: str


class JoinResult(Schema):
    group: Group
    member: Member


class InviteRotationResult(Schema):
    inviteCode: str
    invitePath: str


# ---- Expenses -------------------------------------------------------------


class SplitInput(Schema):
    memberId: str
    raw: float | None = None


class ExpenseShare(Schema):
    memberId: str
    amountMinor: int


class ExpenseInput(Schema):
    description: str
    note: str = ""
    date: Date
    amountMinor: int
    payerMemberId: str
    splitType: SplitType
    participants: Annotated[list[SplitInput], Field(min_length=1)]


class Expense(Schema):
    id: str
    groupId: str
    description: str
    note: str
    amountMinor: int
    date: Date
    payerMemberId: str
    splitType: SplitType
    splitInputs: list[SplitInput]
    shares: list[ExpenseShare]
    createdByUserId: str
    createdAt: datetime
    updatedAt: datetime
    version: int


# ---- Payments -------------------------------------------------------------


class PaymentInput(Schema):
    recipientMemberId: str
    amountMinor: int
    date: Date
    note: str = ""


class Payment(Schema):
    id: str
    groupId: str
    payerMemberId: str
    recipientMemberId: str
    amountMinor: int
    date: Date
    note: str
    createdByUserId: str
    createdAt: datetime
    updatedAt: datetime
    version: int
    canManage: bool


# ---- Balances and settlement ---------------------------------------------


class Balance(Schema):
    memberId: str
    netMinor: int


class Transfer(Schema):
    fromMemberId: str
    toMemberId: str
    amountMinor: int


class SettlementResult(Schema):
    strategy: Strategy
    transfers: list[Transfer]


# ---- Snapshot -------------------------------------------------------------


class GroupSnapshot(Schema):
    group: GroupView
    me: MeRef
    members: list[MemberView]
    expenses: list[Expense]
    payments: list[Payment]
    balances: list[Balance]
