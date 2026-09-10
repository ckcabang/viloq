"""Runtime settings, read from the environment."""

from __future__ import annotations

import os

API_PREFIX = "/api/v1"


def expose_magic_link_token() -> bool:
    """Whether magic-link responses echo the token and client route.

    A real deployment emails the link and must leave this off; the mock backend
    has no mailer, so it defaults on and the frontend can follow the link.
    """
    return os.environ.get("VILOQ_EXPOSE_MAGIC_LINK", "1") not in {"0", "false", "False"}
