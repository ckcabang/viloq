"""The app serves the frontend, and the frontend talks to the contract.

`frontend/js/api.js` is the single place the browser calls the server from, so
the paths it builds are checked against `openapi.yaml` here — a frontend call to
an endpoint the contract does not declare fails the suite rather than the user's
browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import cors_origins, frontend_dir
from tests.conftest import API
from tests.test_contract import spec_operations

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
API_JS = FRONTEND / "js" / "api.js"

# `${encodeURIComponent(groupId)}` in a template literal and `{groupId}` in the
# spec are the same hole; both collapse to this.
_HOLE = "{}"


def frontend_paths() -> set[str]:
    """Every API path `api.js` builds, with its interpolations blanked out."""
    source = API_JS.read_text(encoding="utf-8")
    calls = re.findall(r"request\(\s*(?:'([^']*)'|`([^`]*)`)", source)
    paths = {single or template for single, template in calls}
    return {re.sub(r"\$\{[^}]*\}", _HOLE, path) for path in paths}


def declared_paths() -> set[str]:
    return {re.sub(r"\{[^}]*\}", _HOLE, path) for _, path in spec_operations()}


class TestFrontendBoundary:
    def test_api_js_calls_only_declared_paths(self):
        assert frontend_paths() <= declared_paths()

    def test_every_declared_path_is_reachable_from_the_frontend(self):
        # The UI covers the whole contract; a new endpoint nothing calls is a
        # loose end worth noticing.
        assert declared_paths() <= frontend_paths()

    def test_the_boundary_carries_no_leftover_mock(self):
        source = API_JS.read_text(encoding="utf-8")
        assert "localStorage" not in source
        assert "fetch(" in source


class TestStaticServing:
    def test_the_root_serves_the_app_shell(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert '<div id="app">' in response.text

    def test_the_shell_points_the_client_at_this_api(self, client):
        assert f"VILOQ_API_BASE = '{API}'" in client.get("/").text

    def test_module_assets_are_served(self, client):
        response = client.get("/js/api.js")
        assert response.status_code == 200
        assert "BACKEND BOUNDARY" in response.text

    def test_an_unknown_static_path_still_uses_the_error_shape(self, client):
        response = client.get("/nope.js")
        assert response.status_code == 404
        assert set(response.json()) == {"code", "message"}

    def test_serving_is_skipped_when_there_is_no_frontend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("VILOQ_FRONTEND_DIR", str(tmp_path))
        assert frontend_dir() is None


class TestCors:
    """Only needed when the frontend is hosted somewhere else."""

    def test_a_dev_origin_may_preflight_a_mutation(self, client):
        origin = cors_origins()[0]
        response = client.options(
            f"{API}/groups",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin

    def test_the_etag_header_is_exposed_to_a_cross_origin_caller(
        self, alice, group
    ):
        # The frontend reads ETag off mutation responses to send it back as
        # If-Match; a cross-origin browser only sees it if it is exposed.
        origin = cors_origins()[0]
        response = alice.patch(
            f"/groups/{group.id}",
            json={"name": "Renamed"},
            headers={**alice.if_match(group.version), "Origin": origin},
        )
        assert response.status_code == 200
        exposed = response.headers["access-control-expose-headers"].lower()
        assert "etag" in exposed

    def test_an_unknown_origin_is_not_allowed(self, client):
        response = client.get(
            f"{API}/groups", headers={"Origin": "https://evil.example"}
        )
        assert "access-control-allow-origin" not in response.headers

    def test_origins_can_be_turned_off(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VILOQ_CORS_ORIGINS", "")
        assert cors_origins() == []
