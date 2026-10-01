"""`backend/config.py` — the environment reads that pick the backend at deploy time.

The frontend-serving and CORS reads are covered in `test_frontend.py`; this
pins the database URL, which says which Postgres to use.
"""

from __future__ import annotations

import pytest

from backend.config import DEFAULT_DATABASE_URL, database_url


class TestDatabaseUrl:
    def test_defaults_to_the_compose_postgres(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VILOQ_DATABASE_URL", raising=False)
        assert database_url() == DEFAULT_DATABASE_URL
        assert database_url().startswith("postgresql+psycopg://")

    def test_an_env_var_overrides_the_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VILOQ_DATABASE_URL", "postgresql+psycopg://u:pw@host/viloq")
        assert database_url() == "postgresql+psycopg://u:pw@host/viloq"

    def test_a_blank_env_var_falls_back_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("VILOQ_DATABASE_URL", "   ")
        assert database_url() == DEFAULT_DATABASE_URL

    @pytest.mark.parametrize("scheme", ["postgresql://", "postgres://"])
    def test_a_driverless_url_gets_the_psycopg_driver(
        self, monkeypatch: pytest.MonkeyPatch, scheme: str
    ):
        # The form Render's `connectionString` takes.
        monkeypatch.setenv("VILOQ_DATABASE_URL", f"{scheme}u:pw@host:5432/viloq")
        assert database_url() == "postgresql+psycopg://u:pw@host:5432/viloq"
