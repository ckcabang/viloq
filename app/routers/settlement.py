"""Suggested transfers — recommendations only, never payment records."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.deps import DbDep, GroupIdDep, UserDep, require_group_membership
from app.domain.balances import compute_balances
from app.domain.settle import minimized_transfers, relationship_preserving_transfers
from app.schemas import Error, SettlementResult, Strategy

router = APIRouter(tags=["Settlement"])


@router.get(
    "/groups/{groupId}/settlement",
    response_model=SettlementResult,
    summary="Get suggested settlement transfers",
    responses={
        400: {"model": Error},
        403: {"model": Error},
        404: {"model": Error},
    },
)
def get_settlement(
    groupId: GroupIdDep,
    db: DbDep,
    user: UserDep,
    strategy: Annotated[Strategy, Query()] = "minimized",
) -> SettlementResult:
    require_group_membership(db, user, groupId)

    members = db.members_of(groupId)
    expenses = db.expenses_of(groupId)
    payments = db.payments_of(groupId)

    transfers = (
        relationship_preserving_transfers(members, expenses, payments)
        if strategy == "relationship"
        else minimized_transfers(compute_balances(members, expenses, payments))
    )
    return SettlementResult(strategy=strategy, transfers=transfers)
