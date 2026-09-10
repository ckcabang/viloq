"""POST /auth/magic-links, /verify, GET+PATCH /auth/me, POST /auth/logout."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.db import Database
from tests.conftest import API, Actor, sign_in


class TestRequestMagicLink:
    def test_returns_normalized_email_and_ttl(self, client):
        response = client.post(
            f"{API}/auth/magic-links", json={"email": "  Sam@Example.COM "}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == "sam@example.com"
        assert body["expiresInMinutes"] == 15

    def test_does_not_reveal_whether_the_account_exists(self, client, alice):
        known = client.post(f"{API}/auth/magic-links", json={"email": alice.email})
        unknown = client.post(
            f"{API}/auth/magic-links", json={"email": "nobody@example.com"}
        )
        assert known.status_code == unknown.status_code == 200
        assert known.json().keys() == unknown.json().keys()

    def test_needs_no_session(self, client):
        assert (
            client.post(
                f"{API}/auth/magic-links", json={"email": "sam@example.com"}
            ).status_code
            == 200
        )

    @pytest.mark.parametrize(
        "email", ["", "   ", "not-an-email", "no@domain", "two@@example.com", "a b@c.d"]
    )
    def test_rejects_a_malformed_address(self, client, email):
        response = client.post(f"{API}/auth/magic-links", json={"email": email})
        assert response.status_code == 400
        assert response.json()["code"] == "validation"

    def test_missing_field_is_a_400_not_a_422(self, client):
        response = client.post(f"{API}/auth/magic-links", json={})
        assert response.status_code == 400
        assert response.json()["code"] == "validation"

    def test_mock_backend_echoes_the_token_and_client_route(self, client):
        body = client.post(
            f"{API}/auth/magic-links", json={"email": "echo@example.com"}
        ).json()
        assert body["token"]
        assert body["magicLinkPath"] == f"#/auth/verify?token={body['token']}"

    def test_a_real_deployment_can_withhold_the_token(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("VILOQ_EXPOSE_MAGIC_LINK", "0")
        body = client.post(
            f"{API}/auth/magic-links", json={"email": "quiet@example.com"}
        ).json()
        assert body["token"] is None
        assert body["magicLinkPath"] is None
        assert body["expiresInMinutes"] == 15


class TestVerifyMagicLink:
    def test_first_sign_in_creates_the_user_and_asks_for_a_name(self, client):
        token = client.post(
            f"{API}/auth/magic-links", json={"email": "new@example.com"}
        ).json()["token"]

        response = client.post(f"{API}/auth/magic-links/verify", json={"token": token})
        assert response.status_code == 200
        body = response.json()
        assert body["sessionToken"]
        assert body["needsDisplayName"] is True
        assert body["user"]["email"] == "new@example.com"
        assert body["user"]["displayName"] == ""
        assert body["user"]["emailVerified"] is True

    def test_returning_user_keeps_their_id_and_name(self, client, alice):
        alice.patch("/auth/me", json={"displayName": "Alice Kim"})
        again = sign_in(client, alice.email)
        assert again.user_id == alice.user_id
        assert again.token != alice.token

        body = client.post(
            f"{API}/auth/magic-links",
            json={"email": alice.email},
        ).json()
        verified = client.post(
            f"{API}/auth/magic-links/verify", json={"token": body["token"]}
        ).json()
        assert verified["needsDisplayName"] is False
        assert verified["user"]["displayName"] == "Alice Kim"

    def test_token_is_single_use(self, client):
        token = client.post(
            f"{API}/auth/magic-links", json={"email": "sam@example.com"}
        ).json()["token"]
        assert (
            client.post(
                f"{API}/auth/magic-links/verify", json={"token": token}
            ).status_code
            == 200
        )

        replay = client.post(f"{API}/auth/magic-links/verify", json={"token": token})
        assert replay.status_code == 400
        assert replay.json()["code"] == "invalid_token"

    def test_unknown_token_is_invalid(self, client):
        response = client.post(
            f"{API}/auth/magic-links/verify", json={"token": "nope"}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_token"

    def test_expired_token_reports_expiry(self, client, db: Database):
        token = client.post(
            f"{API}/auth/magic-links", json={"email": "sam@example.com"}
        ).json()["token"]
        link = db.magic_link(token)
        link.expires_at = link.expires_at - timedelta(minutes=16)

        response = client.post(f"{API}/auth/magic-links/verify", json={"token": token})
        assert response.status_code == 400
        assert response.json()["code"] == "expired_token"


class TestCurrentUser:
    def test_returns_the_signed_in_user(self, alice: Actor):
        response = alice.get("/auth/me")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == alice.user_id
        assert body["email"] == alice.email
        assert set(body) == {
            "id",
            "email",
            "displayName",
            "emailVerified",
            "createdAt",
            "updatedAt",
        }

    def test_requires_a_session(self, client):
        response = client.get(f"{API}/auth/me")
        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"

    def test_rejects_an_unknown_token(self, client):
        response = client.get(
            f"{API}/auth/me", headers={"Authorization": "Bearer made-up"}
        )
        assert response.status_code == 401

    def test_rejects_a_non_bearer_scheme(self, client, alice: Actor):
        response = client.get(
            f"{API}/auth/me", headers={"Authorization": f"Basic {alice.token}"}
        )
        assert response.status_code == 401


class TestUpdateDisplayName:
    def test_trims_and_persists(self, alice: Actor):
        response = alice.patch("/auth/me", json={"displayName": "  Sam  "})
        assert response.status_code == 200
        assert response.json()["displayName"] == "Sam"
        assert alice.get("/auth/me").json()["displayName"] == "Sam"

    @pytest.mark.parametrize("name", ["", "   "])
    def test_rejects_an_empty_name(self, alice: Actor, name):
        response = alice.patch("/auth/me", json={"displayName": name})
        assert response.status_code == 400
        assert response.json()["code"] == "validation"

    def test_requires_a_session(self, client):
        assert (
            client.patch(f"{API}/auth/me", json={"displayName": "Sam"}).status_code
            == 401
        )

    def test_does_not_rename_existing_group_memberships(self, alice: Actor, group):
        alice.patch("/auth/me", json={"displayName": "Renamed"})
        snapshot = alice.get(f"/groups/{group.id}").json()
        assert snapshot["me"]["displayName"] == "Alice"


class TestSignOut:
    def test_invalidates_the_session(self, alice: Actor):
        assert alice.post("/auth/logout").status_code == 204
        assert alice.get("/auth/me").status_code == 401

    def test_is_idempotent(self, alice: Actor):
        alice.post("/auth/logout")
        assert alice.post("/auth/logout").status_code == 204

    def test_succeeds_without_any_token(self, client):
        assert client.post(f"{API}/auth/logout").status_code == 204

    def test_leaves_group_memberships_intact(self, client, alice: Actor, group):
        alice.post("/auth/logout")
        again = sign_in(client, alice.email)
        assert [g["id"] for g in again.get("/groups").json()] == [group.id]
