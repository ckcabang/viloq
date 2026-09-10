"""Invite code lookup, joining, rotation, and revocation."""

from __future__ import annotations

from fastapi import APIRouter, Path, Response, status
from typing import Annotated

from app import errors, views
from app.db import now, random_id
from app.deps import (
    DbDep,
    GroupIdDep,
    UserDep,
    require_creator,
    require_group_membership,
)
from app.models import Member as MemberRecord
from app.schemas import Error, InviteInfo, InviteRotationResult, JoinRequest, JoinResult

router = APIRouter(tags=["Invites"])

InviteCodeDep = Annotated[str, Path(alias="code")]


@router.get(
    "/invites/{code}",
    response_model=InviteInfo,
    summary="Look up a group from an invite code",
    responses={404: {"model": Error}},
)
def get_invite_info(code: InviteCodeDep, db: DbDep) -> InviteInfo:
    group = db.group_by_invite_code(code)
    if group is None:
        raise errors.not_found("This invite code is not valid.")
    return InviteInfo(
        groupId=group.id,
        groupName=group.name,
        currency=group.currency,
        revoked=group.invite_revoked,
        memberCount=len(db.members_of(group.id)),
    )


@router.post(
    "/invites/{code}/join",
    response_model=JoinResult,
    summary="Join a group via invite code",
    responses={400: {"model": Error}, 403: {"model": Error}, 404: {"model": Error}},
)
def join_group(
    code: InviteCodeDep, body: JoinRequest, db: DbDep, user: UserDep
) -> JoinResult:
    name = (body.displayName or "").strip()
    if not name:
        raise errors.validation("Display name cannot be empty.")

    with db.transaction():
        group = db.group_by_invite_code(code)
        if group is None:
            raise errors.not_found("This invite code is not valid.")
        if group.invite_revoked:
            raise errors.ApiError(
                403, "invite_revoked", "This invite has been revoked. Ask for a new one."
            )

        member = db.membership(user.id, group.id)
        if member is None:
            stamp = now()
            member = db.add_member(
                MemberRecord(
                    id=random_id("mbr"),
                    group_id=group.id,
                    user_id=user.id,
                    display_name=name,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )

    return JoinResult(group=views.group(group), member=views.member(member))


@router.post(
    "/groups/{groupId}/invite",
    response_model=InviteRotationResult,
    summary="Rotate the group's invite code",
    responses={403: {"model": Error}, 404: {"model": Error}},
)
def regenerate_invite(
    groupId: GroupIdDep, db: DbDep, user: UserDep
) -> InviteRotationResult:
    with db.transaction():
        group, _ = require_group_membership(db, user, groupId)
        require_creator(group, user)
        group.invite_code = db.unique_invite_code()
        group.invite_token = random_id("tok")
        group.invite_revoked = False
        group.updated_at = now()

    return InviteRotationResult(
        inviteCode=group.invite_code, invitePath=views.invite_path(group.invite_code)
    )


@router.delete(
    "/groups/{groupId}/invite",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the group's current invite code",
    responses={403: {"model": Error}, 404: {"model": Error}},
)
def revoke_invite(groupId: GroupIdDep, db: DbDep, user: UserDep) -> Response:
    with db.transaction():
        group, _ = require_group_membership(db, user, groupId)
        require_creator(group, user)
        group.invite_revoked = True
        group.updated_at = now()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
