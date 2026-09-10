"""Shared request dependencies: session auth, membership, and `If-Match`."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import Depends, Header, Path

from app import errors
from app.db import Database, get_db
from app.models import Group, Member, User

DbDep = Annotated[Database, Depends(get_db)]

_BEARER = re.compile(r"^Bearer\s+(?P<token>\S+)$", re.IGNORECASE)
# RFC 7232 entity-tag, optionally weak: W/"3" or "3".
_ETAG = re.compile(r'^(?:W/)?"(?P<value>[^"]*)"$')


def session_token(
    authorization: Annotated[str | None, Header()] = None,
) -> str | None:
    """The bearer token, or None when the header is missing or malformed."""
    if not authorization:
        return None
    match = _BEARER.match(authorization.strip())
    return match.group("token") if match else None


TokenDep = Annotated[str | None, Depends(session_token)]


def current_user(db: DbDep, token: TokenDep) -> User:
    user = db.user_for_session(token)
    if user is None:
        raise errors.unauthorized()
    return user


UserDep = Annotated[User, Depends(current_user)]


def if_match(
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> int:
    """Parse the required `If-Match` version.

    Missing or unparseable yields `412 precondition_required` — without a usable
    version there is no precondition to evaluate, so the request is refused
    rather than silently treated as unconditional.
    """
    if if_match is None:
        raise errors.precondition_required()
    match = _ETAG.match(if_match.strip())
    raw = match.group("value") if match else if_match.strip()
    try:
        return int(raw)
    except ValueError:
        raise errors.precondition_required() from None


IfMatchDep = Annotated[int, Depends(if_match)]

GroupIdDep = Annotated[str, Path(alias="groupId")]


def require_group(db: Database, group_id: str) -> Group:
    group = db.group(group_id)
    if group is None:
        raise errors.not_found("Group not found.")
    return group


def require_membership(db: Database, user: User, group_id: str) -> Member:
    member = db.membership(user.id, group_id)
    if member is None:
        raise errors.forbidden("You are not a member of this group.")
    return member


def require_group_membership(
    db: Database, user: User, group_id: str
) -> tuple[Group, Member]:
    """Group must exist (404) before membership is judged (403)."""
    group = require_group(db, group_id)
    return group, require_membership(db, user, group_id)


def require_creator(group: Group, user: User) -> None:
    if group.created_by_user_id != user.id:
        raise errors.forbidden("Only the group creator can do that.")


def check_version(record_version: int, expected: int, current: dict) -> None:
    if record_version != expected:
        raise errors.version_conflict(current)
