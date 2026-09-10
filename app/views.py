"""Storage record -> wire shape.

One function per entity so every endpoint that returns e.g. an expense returns
the identical shape ("one shape per entity", per the contract's preamble).
"""

from __future__ import annotations

from urllib.parse import quote

from app import models, schemas


def invite_path(code: str) -> str:
    return f"#/join?code={quote(code, safe='')}"


def user(record: models.User) -> schemas.User:
    return schemas.User(
        id=record.id,
        email=record.email,
        displayName=record.display_name,
        emailVerified=record.email_verified,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


def group(record: models.Group) -> schemas.Group:
    return schemas.Group(
        id=record.id,
        name=record.name,
        currency=record.currency,
        createdByUserId=record.created_by_user_id,
        inviteCode=record.invite_code,
        inviteRevoked=record.invite_revoked,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
        version=record.version,
    )


def group_view(
    record: models.Group, *, viewer_user_id: str, currency_locked: bool
) -> schemas.GroupView:
    return schemas.GroupView(
        **group(record).model_dump(),
        isCreator=record.created_by_user_id == viewer_user_id,
        currencyLocked=currency_locked,
        invitePath=invite_path(record.invite_code),
    )


def member(record: models.Member) -> schemas.Member:
    return schemas.Member(
        id=record.id,
        groupId=record.group_id,
        userId=record.user_id,
        displayName=record.display_name,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


def member_view(record: models.Member, *, viewer_user_id: str) -> schemas.MemberView:
    return schemas.MemberView(
        **member(record).model_dump(), isMe=record.user_id == viewer_user_id
    )


def expense(record: models.Expense) -> schemas.Expense:
    return schemas.Expense(
        id=record.id,
        groupId=record.group_id,
        description=record.description,
        note=record.note,
        amountMinor=record.amount_minor,
        date=record.date,
        payerMemberId=record.payer_member_id,
        splitType=record.split_type,
        splitInputs=[
            schemas.SplitInput(memberId=s.member_id, raw=s.raw)
            for s in record.split_inputs
        ],
        shares=[
            schemas.ExpenseShare(memberId=s.member_id, amountMinor=s.amount_minor)
            for s in record.shares
        ],
        createdByUserId=record.created_by_user_id,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
        version=record.version,
    )


def payment(record: models.Payment, *, viewer_user_id: str) -> schemas.Payment:
    return schemas.Payment(
        id=record.id,
        groupId=record.group_id,
        payerMemberId=record.payer_member_id,
        recipientMemberId=record.recipient_member_id,
        amountMinor=record.amount_minor,
        date=record.date,
        note=record.note,
        createdByUserId=record.created_by_user_id,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
        version=record.version,
        canManage=record.created_by_user_id == viewer_user_id,
    )


def conflict_payload(model: schemas.Schema) -> dict:
    """The `current` body of a 409, JSON-safe (dates as strings)."""
    return model.model_dump(mode="json")
