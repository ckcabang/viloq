"""`python -m backend.issue_link` — operator-issued sign-in links."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.db import Database, now
from backend.issue_link import DEFAULT_BASE_URL, base_url, issue_link, main
from tests.conftest import API


def token_of(url: str) -> str:
    fragment = urlsplit(url).fragment  # "/auth/verify?token=..."
    return parse_qs(urlsplit(fragment).query)["token"][0]


class TestIssueLink:
    def test_points_the_frontend_verify_route_at_the_base_url(self, db: Database):
        url = issue_link(db, "sam@example.com", "https://viloq.example/", 60)
        token = token_of(url)
        assert url == f"https://viloq.example/#/auth/verify?token={token}"

    def test_the_link_signs_in_through_the_api(self, client, db: Database):
        url = issue_link(db, "  Sam@Example.COM ", DEFAULT_BASE_URL, 60)

        response = client.post(
            f"{API}/auth/magic-links/verify", json={"token": token_of(url)}
        )
        assert response.status_code == 200
        assert response.json()["user"]["email"] == "sam@example.com"

    def test_the_link_is_single_use(self, client, db: Database):
        token = token_of(issue_link(db, "sam@example.com", DEFAULT_BASE_URL, 60))
        client.post(f"{API}/auth/magic-links/verify", json={"token": token})

        replay = client.post(f"{API}/auth/magic-links/verify", json={"token": token})
        assert replay.status_code == 400
        assert replay.json()["code"] == "invalid_token"

    def test_lasts_as_long_as_asked(self, db: Database):
        token = token_of(issue_link(db, "sam@example.com", DEFAULT_BASE_URL, 600))
        remaining = db.magic_link(token).expires_at - now()
        assert timedelta(minutes=599) < remaining <= timedelta(minutes=600)

    @pytest.mark.parametrize("email", ["", "not-an-email", "a b@c.d"])
    def test_rejects_a_malformed_address(self, db: Database, email):
        with pytest.raises(ValueError):
            issue_link(db, email, DEFAULT_BASE_URL, 60)

    def test_rejects_a_lifetime_under_a_minute(self, db: Database):
        with pytest.raises(ValueError):
            issue_link(db, "sam@example.com", DEFAULT_BASE_URL, 0)


class TestBaseUrl:
    def test_uses_the_render_url_when_there_is_one(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://viloq.onrender.com")
        assert base_url() == "https://viloq.onrender.com"

    def test_falls_back_to_localhost(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
        assert base_url() == DEFAULT_BASE_URL


class TestMain:
    def test_prints_a_working_link(self, client, capsys: pytest.CaptureFixture):
        assert main(["sam@example.com", "--base-url", "https://viloq.example"]) == 0

        url = capsys.readouterr().out.strip()
        assert url.startswith("https://viloq.example/#/auth/verify?token=")
        response = client.post(
            f"{API}/auth/magic-links/verify", json={"token": token_of(url)}
        )
        assert response.status_code == 200

    def test_a_bad_address_is_a_usage_error(self, capsys: pytest.CaptureFixture):
        with pytest.raises(SystemExit) as exit_:
            main(["not-an-email"])
        assert exit_.value.code == 2
        assert "Not a valid email address" in capsys.readouterr().err
