"""`/healthz`: what Render and the deploy pipeline poll to call a deploy live."""

from __future__ import annotations

from sqlalchemy.exc import OperationalError

from backend.db import Database, get_db
from tests.conftest import API


class TestHealth:
    def test_up_when_the_database_answers(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_reports_the_image_ci_built(self, client, monkeypatch):
        monkeypatch.setenv("VILOQ_COMMIT", "83242da" + "0" * 33)
        monkeypatch.setenv("VILOQ_IMAGE_TAG", "20260818-163457-83242da")
        body = client.get("/healthz").json()
        assert body["commit"] == "83242da" + "0" * 33
        assert body["image"] == "20260818-163457-83242da"

    def test_no_build_outside_ci(self, client, monkeypatch):
        monkeypatch.delenv("VILOQ_COMMIT", raising=False)
        monkeypatch.delenv("VILOQ_IMAGE_TAG", raising=False)
        body = client.get("/healthz").json()
        assert body["commit"] is None
        assert body["image"] is None

    def test_unavailable_when_the_database_is_not(self, app, client, db):
        class Unreachable(Database):
            def ping(self) -> None:
                raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        app.dependency_overrides[get_db] = lambda: Unreachable(db.session)
        response = client.get("/healthz")
        assert response.status_code == 503
        assert response.json()["status"] == "unavailable"

    def test_stays_out_of_the_contract(self, client):
        paths = client.get(f"{API}/openapi.json").json()["paths"]
        assert not any(path.endswith("/healthz") for path in paths)
