"""Runtime settings, read from the environment."""

from __future__ import annotations

import os
from pathlib import Path

API_PREFIX = "/api/v1"

REPO_ROOT = Path(__file__).resolve().parents[1]

# Origins allowed to call the API cross-origin. Only needed when the frontend is
# served from somewhere other than this app (the `python -m http.server` dev
# flow); serving it from here needs no CORS at all. Set to "" to allow none.
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"

# A SQLAlchemy URL, so the backend is chosen by configuration rather than by
# code: `postgresql+psycopg://user:pw@host/viloq` needs no change here beyond
# installing the driver. Relative SQLite paths are relative to the working
# directory, so the default lands beside wherever the server was started.
DEFAULT_DATABASE_URL = "sqlite+pysqlite:///./viloq.db"


def database_url() -> str:
    """Where to store data. Any SQLAlchemy-supported database will do."""
    return os.environ.get("VILOQ_DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL


def expose_magic_link_token() -> bool:
    """Whether magic-link responses echo the token and client route.

    A real deployment emails the link and must leave this off; the mock backend
    has no mailer, so it defaults on and the frontend can follow the link.
    """
    return os.environ.get("VILOQ_EXPOSE_MAGIC_LINK", "1") not in {"0", "false", "False"}


def frontend_dir() -> Path | None:
    """The static frontend to serve at `/`, or None when there is none.

    Serving the page from the API means one origin, so the browser's relative
    `/api/v1` calls just work.
    """
    override = os.environ.get("VILOQ_FRONTEND_DIR")
    directory = Path(override) if override else REPO_ROOT / "frontend"
    return directory if (directory / "index.html").is_file() else None


def cors_origins() -> list[str]:
    raw = os.environ.get("VILOQ_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
