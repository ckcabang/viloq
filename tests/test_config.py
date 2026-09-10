"""`app/config.py` — the environment reads that pick the backend at deploy time.

The frontend-serving and CORS reads are covered in `test_frontend.py`; this
pins the database URL, which is the switch that lets Postgres drop in without a
code change.
"""

from __future__ import annotations

import pytest

from app.config import DEFAULT_DATABASE_URL, database_url


class TestDatabaseUrl:
    def test_defaults_to_a_local_sqlite_file(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VILOQ_DATABASE_URL", raising=False)
        assert database_url() == DEFAULT_DATABASE_URL
        assert database_url().startswith("sqlite")

    def test_an_env_var_overrides_the_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VILOQ_DATABASE_URL", "postgresql+psycopg://u:pw@host/viloq")
        assert database_url() == "postgresql+psycopg://u:pw@host/viloq"

    def test_a_blank_env_var_falls_back_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("VILOQ_DATABASE_URL", "   ")
        assert database_url() == DEFAULT_DATABASE_URL
