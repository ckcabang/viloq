"""Group list, creation, the dashboard snapshot, and settings."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app import errors, views
from app.db import now, random_id
from app.deps import (
    DbDep,
    GroupIdDep,
    IfMatchDep,
    UserDep,
    check_version,
    require_creator,
    require_group_membership,
)
from app.domain.balances import compute_balances
from app.models import Group as GroupRecord
from app.models import Member as MemberRecord
from app.schemas import (
    CreateGroupRequest,
    DisplayNameRequest,
    Error,
    Group,
    GroupSnapshot,
    GroupSummary,
    MeRef,
    Member,
    UpdateGroupRequest,
)

router = APIRouter(tags=["Groups"])


def set_etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'


@router.get(
    "/groups",
    response_model=list[GroupSummary],
    summary="List the groups the current user belongs to",
)
def list_groups(db: DbDep, user: UserDep) -> list[GroupSummary]:
    summaries: list[GroupSummary] = []
    for membership in db.memberships_of_user(user.id):
        group = db.group(membership.group_id)
        if group is None:
            continue
        members = db.members_of(group.id)
        expenses = db.expenses_of(group.id)
        balances = compute_balances(members, expenses, db.payments_of(group.id))
        my_net = next(
            (b["netMinor"] for b in balances if b["memberId"] == membership.id), 0
        )
        summaries.append(
            GroupSummary(
                id=group.id,
                name=group.name,
                currency=group.currency,
                memberCount=len(members),
                expenseCount=len(expenses),
                myMemberId=membership.id,
                myDisplayName=membership.display_name,
                myNetMinor=my_net,
            )
        )
    return sorted(summaries, key=lambda s: (s.name.casefold(), s.id))


@router.post(
    "/groups",
    response_model=Group,
    status_code=status.HTTP_201_CREATED,
    summary="Create a group",
    responses={400: {"model": Error}},
)
def create_group(
    body: CreateGroupRequest, db: DbDep, user: UserDep, response: Response
) -> Group:
    group_name = (body.name or "").strip()
    member_name = (body.displayName or "").strip()
    if not group_name:
        raise errors.validation("Group name is required.")
    if not member_name:
        raise errors.validation("Your display name for this group is required.")

    stamp = now()
    with db.lock:
        group = db.add_group(
            GroupRecord(
                id=random_id("grp"),
                name=group_name,
                currency=body.currency,
                created_by_user_id=user.id,
                invite_code=db.unique_invite_code(),
                invite_token=random_id("tok"),
                invite_revoked=False,
                created_at=stamp,
                updated_at=stamp,
                version=1,
            )
        )
        db.add_member(
            MemberRecord(
                id=random_id("mbr"),
                group_id=group.id,
                user_id=user.id,
                display_name=member_name,
                created_at=stamp,
                updated_at=stamp,
            )
        )

    set_etag(response, group.version)
    return views.group(group)



@router.get(
    "/groups/{groupId}",
    response_model=GroupSnapshot,
    summary="Get the full group dashboard snapshot",
    responses={403: {"model": Error}, 404: {"model": Error}},
)
def get_group_snapshot(
    groupId: GroupIdDep, db: DbDep, user: UserDep
) -> GroupSnapshot:
    group, me = require_group_membership(db, user, groupId)

    members = db.members_of(group.id)
    expenses = db.expenses_of(group.id)
    payments = db.payments_of(group.id)

    return GroupSnapshot(
        group=views.group_view(
            group, viewer_user_id=user.id, currency_locked=bool(expenses)
        ),
        me=MeRef(memberId=me.id, displayName=me.display_name, userId=user.id),
        members=[
            views.member_view(m, viewer_user_id=user.id)
            for m in sorted(members, key=lambda m: (m.display_name.casefold(), m.id))
        ],
        expenses=[
            views.expense(e)
            for e in sorted(
                expenses, key=lambda e: (e.date, e.created_at, e.id), reverse=True
            )
        ],
        payments=[
            views.payment(p, viewer_user_id=user.id)
            for p in sorted(
                payments, key=lambda p: (p.date, p.created_at, p.id), reverse=True
            )
        ],
        balances=compute_balances(members, expenses, payments),
    )


@router.patch(
    "/groups/{groupId}",
    response_model=Group,
    summary="Update group name and/or currency",
    responses={
        400: {"model": Error},
        403: {"model": Error},
        404: {"model": Error},
        409: {"model": Error},
        412: {"model": Error},
    },
)
def update_group(
    groupId: GroupIdDep,
    body: UpdateGroupRequest,
    expected_version: IfMatchDep,
    db: DbDep,
    user: UserDep,
    response: Response,
) -> Group:
    with db.lock:
        group, _ = require_group_membership(db, user, groupId)
        require_creator(group, user)
        check_version(
            group.version,
            expected_version,
            views.conflict_payload(views.group(group)),
        )

        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise errors.validation("Group name cannot be empty.")
            group.name = name

        if body.currency is not None and body.currency != group.currency:
            if db.expenses_of(group.id):
                raise errors.currency_locked()
            group.currency = body.currency

        group.updated_at = now()
        group.version += 1

    set_etag(response, group.version)
    return views.group(group)


@router.patch(
    "/groups/{groupId}/me",
    response_model=Member,
    tags=["Members"],
    summary="Update the caller's display name within this group",
    responses={400: {"model": Error}, 403: {"model": Error}, 404: {"model": Error}},
)
def update_my_member_name(
    groupId: GroupIdDep,
    body: DisplayNameRequest,
    db: DbDep,
    user: UserDep,
) -> Member:
    _, me = require_group_membership(db, user, groupId)
    name = (body.displayName or "").strip()
    if not name:
        raise errors.validation("Display name cannot be empty.")
    me.display_name = name
    me.updated_at = now()
    return views.member(me)
