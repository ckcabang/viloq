"""Passwordless magic-link login and the current-user record."""

from __future__ import annotations

import re

from fastapi import APIRouter, Response, status

from app import errors, views
from app.config import expose_magic_link_token
from app.db import MAGIC_LINK_TTL_MINUTES, now
from app.deps import DbDep, TokenDep, UserDep
from app.schemas import (
    DisplayNameRequest,
    Error,
    MagicLinkRequest,
    MagicLinkRequestResult,
    User,
    VerifyRequest,
    VerifyResult,
)

router = APIRouter(tags=["Auth"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post(
    "/auth/magic-links",
    response_model=MagicLinkRequestResult,
    summary="Request a magic sign-in link",
    responses={400: {"model": Error}},
)
def request_magic_link(body: MagicLinkRequest, db: DbDep) -> MagicLinkRequestResult:
    email = (body.email or "").strip().lower()
    if not _EMAIL.match(email):
        raise errors.validation("Enter a valid email address.")

    link = db.create_magic_link(email)
    result = MagicLinkRequestResult(
        email=email, expiresInMinutes=MAGIC_LINK_TTL_MINUTES
    )
    if expose_magic_link_token():
        # In production the link is emailed; here it comes back so a local UI
        # can render it.
        result.token = link.token
        result.magicLinkPath = f"#/auth/verify?token={link.token}"
    return result


@router.post(
    "/auth/magic-links/verify",
    response_model=VerifyResult,
    summary="Exchange a magic-link token for a session",
    responses={400: {"model": Error}},
)
def verify_magic_link(body: VerifyRequest, db: DbDep) -> VerifyResult:
    with db.lock:
        link = db.magic_link(body.token or "")
        if link is None:
            raise errors.ApiError(
                400, "invalid_token", "This link is not valid. Request a new one."
            )
        if link.used:
            raise errors.ApiError(
                400, "invalid_token", "This link was already used. Request a new one."
            )
        if now() > link.expires_at:
            raise errors.ApiError(
                400, "expired_token", "This link expired. Request a new one."
            )

        link.used = True
        user = db.user_by_email(link.email) or db.create_user(link.email)
        user.email_verified = True
        session = db.create_session(user.id)

    return VerifyResult(
        sessionToken=session.token,
        user=views.user(user),
        needsDisplayName=not user.display_name,
    )


@router.get("/auth/me", response_model=User, summary="Get the signed-in user")
def get_current_user(user: UserDep) -> User:
    return views.user(user)


@router.patch(
    "/auth/me",
    response_model=User,
    summary="Update the account-level display name",
    responses={400: {"model": Error}},
)
def update_display_name(body: DisplayNameRequest, user: UserDep) -> User:
    name = (body.displayName or "").strip()
    if not name:
        raise errors.validation("Display name cannot be empty.")
    user.display_name = name
    user.updated_at = now()
    return views.user(user)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate the current session",
)
def sign_out(db: DbDep, token: TokenDep) -> Response:
    # Idempotent: a missing or already-invalid token is still a 204.
    db.delete_session(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
