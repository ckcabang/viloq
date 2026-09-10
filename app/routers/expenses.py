"""Create / edit / delete expenses with server-resolved split allocation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from app import errors, views
from app.db import Database, now, random_id
from app.deps import (
    DbDep,
    GroupIdDep,
    IfMatchDep,
    UserDep,
    check_version,
    require_group_membership,
)
from app.domain.split import compute_allocation
from app.models import Expense as ExpenseRecord
from app.models import ExpenseShare, SplitInput
from app.schemas import Error, Expense, ExpenseInput

router = APIRouter(tags=["Expenses"])

ExpenseIdDep = Annotated[str, Path(alias="expenseId")]


def _resolve(db: Database, group_id: str, body: ExpenseInput) -> dict:
    """Validate an expense payload and resolve its split into exact shares."""
    member_ids = {m.id for m in db.members_of(group_id)}

    description = (body.description or "").strip()
    if not description:
        raise errors.validation("Description is required.")
    if not body.amountMinor > 0:
        raise errors.validation("Amount must be greater than zero.")
    if body.payerMemberId not in member_ids:
        raise errors.validation("Payer must be a group member.")

    # Entries for people who are no longer members are dropped before the
    # allocation is checked, so a stale client form still reconciles.
    participants = [p for p in body.participants if p.memberId in member_ids]
    if not participants:
        raise errors.validation("Select at least one participant.")

    allocation = compute_allocation(
        split_type=body.splitType,
        amount_minor=body.amountMinor,
        participants=[(p.memberId, p.raw) for p in participants],
    )
    if not allocation.ok:
        raise errors.validation(
            allocation.error or "Split does not reconcile to the total."
        )

    return {
        "description": description,
        "note": (body.note or "").strip(),
        "amount_minor": body.amountMinor,
        "date": body.date,
        "payer_member_id": body.payerMemberId,
        "split_type": body.splitType,
        "split_inputs": [SplitInput(member_id=p.memberId, raw=p.raw) for p in participants],
        "shares": [
            ExpenseShare(member_id=s.member_id, amount_minor=s.amount_minor)
            for s in allocation.shares
        ],
    }


def _require_expense(db: Database, group_id: str, expense_id: str) -> ExpenseRecord:
    expense = db.expense(expense_id)
    if expense is None or expense.group_id != group_id:
        raise errors.not_found("Expense not found.")
    return expense


@router.post(
    "/groups/{groupId}/expenses",
    response_model=Expense,
    status_code=status.HTTP_201_CREATED,
    summary="Add an expense",
    responses={400: {"model": Error}, 403: {"model": Error}, 404: {"model": Error}},
)
def create_expense(
    groupId: GroupIdDep,
    body: ExpenseInput,
    db: DbDep,
    user: UserDep,
    response: Response,
) -> Expense:
    with db.transaction():
        require_group_membership(db, user, groupId)
        resolved = _resolve(db, groupId, body)
        stamp = now()
        expense = db.add_expense(
            ExpenseRecord(
                id=random_id("exp"),
                group_id=groupId,
                created_by_user_id=user.id,
                created_at=stamp,
                updated_at=stamp,
                version=1,
                **resolved,
            )
        )

    response.headers["ETag"] = f'"{expense.version}"'
    return views.expense(expense)


@router.put(
    "/groups/{groupId}/expenses/{expenseId}",
    response_model=Expense,
    summary="Replace an expense",
    responses={
        400: {"model": Error},
        403: {"model": Error},
        404: {"model": Error},
        409: {"model": Error},
        412: {"model": Error},
    },
)
def update_expense(
    groupId: GroupIdDep,
    expenseId: ExpenseIdDep,
    body: ExpenseInput,
    expected_version: IfMatchDep,
    db: DbDep,
    user: UserDep,
    response: Response,
) -> Expense:
    with db.transaction():
        require_group_membership(db, user, groupId)
        expense = _require_expense(db, groupId, expenseId)
        check_version(
            expense.version,
            expected_version,
            views.conflict_payload(views.expense(expense)),
        )
        for key, value in _resolve(db, groupId, body).items():
            setattr(expense, key, value)
        expense.updated_at = now()
        expense.version += 1

    response.headers["ETag"] = f'"{expense.version}"'
    return views.expense(expense)


@router.delete(
    "/groups/{groupId}/expenses/{expenseId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an expense",
    responses={
        403: {"model": Error},
        404: {"model": Error},
        409: {"model": Error},
        412: {"model": Error},
    },
)
def delete_expense(
    groupId: GroupIdDep,
    expenseId: ExpenseIdDep,
    expected_version: IfMatchDep,
    db: DbDep,
    user: UserDep,
) -> Response:
    with db.transaction():
        require_group_membership(db, user, groupId)
        expense = _require_expense(db, groupId, expenseId)
        check_version(
            expense.version,
            expected_version,
            views.conflict_payload(views.expense(expense)),
        )
        db.delete_expense(expense.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
