"""Print a sign-in link for an email address, for an operator to pass on by hand.

A deploy with no mailer keeps `VILOQ_EXPOSE_MAGIC_LINK` off, so nobody can sign
in through the UI there. Whoever can open a shell on the server can still let
someone in: this makes the same single-use link the mailer would send, and
prints it.

    python -m backend.issue_link alice@example.com
    python -m backend.issue_link alice@example.com --minutes 60 --base-url https://...

The link points at `--base-url`, else `RENDER_EXTERNAL_URL` (which Render sets
on every web service), else `http://localhost:8000`.
"""

from __future__ import annotations

import argparse
import os
import sys

from backend.db import Database, engine, init_db, new_session_factory
from backend.routers.auth import EMAIL_PATTERN

DEFAULT_BASE_URL = "http://localhost:8000"

# Longer than an emailed link's 15 minutes: one passed on in a chat may not be
# opened for hours. It is still single-use.
DEFAULT_TTL_MINUTES = 24 * 60


def base_url() -> str:
    return os.environ.get("RENDER_EXTERNAL_URL", "").strip() or DEFAULT_BASE_URL


def issue_link(db: Database, email: str, base: str, ttl_minutes: int) -> str:
    """Store a fresh magic link for `email` and return its full URL."""
    email = email.strip().lower()
    if not EMAIL_PATTERN.match(email):
        raise ValueError(f"Not a valid email address: {email!r}")
    if ttl_minutes < 1:
        raise ValueError("The link must last at least a minute.")

    link = db.create_magic_link(email, ttl_minutes=ttl_minutes)
    return f"{base.rstrip('/')}/#/auth/verify?token={link.token}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.issue_link",
        description="Print a single-use sign-in link for an email address.",
    )
    parser.add_argument("email")
    parser.add_argument(
        "--minutes",
        type=int,
        default=DEFAULT_TTL_MINUTES,
        help=f"how long the link stays valid (default {DEFAULT_TTL_MINUTES})",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"the app's URL (default $RENDER_EXTERNAL_URL, else {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args(argv)

    init_db()
    with new_session_factory(engine())() as session:
        try:
            url = issue_link(
                Database(session), args.email, args.base_url or base_url(), args.minutes
            )
        except ValueError as exc:
            parser.error(str(exc))

    print(url)
    print(f"Single-use; valid for {args.minutes} minutes.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
