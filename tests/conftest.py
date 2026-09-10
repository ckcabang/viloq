"""Shared fixtures.

Every test gets a clean mock database and a small `Actor` helper that wraps the
TestClient with a session token, so tests read as "alice does X" rather than as
header plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import Database, get_db
from app.main import app as fastapi_app

API = "/api/v1"


@pytest.fixture
def db() -> Database:
    return Database()


@pytest.fixture
def app(db: Database):
    fastapi_app.dependency_overrides[get_db] = lambda: db
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@dataclass
class Actor:
    """A signed-in user bound to a TestClient."""

    client: TestClient
    email: str
    user_id: str
    token: str

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def request(self, method: str, path: str, **kwargs: Any):
        headers = {**self.auth, **kwargs.pop("headers", {})}
        return self.client.request(method, f"{API}{path}", headers=headers, **kwargs)

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw):
        return self.request("PATCH", path, **kw)

    def put(self, path: str, **kw):
        return self.request("PUT", path, **kw)

    def delete(self, path: str, **kw):
        return self.request("DELETE", path, **kw)

    def if_match(self, version: int) -> dict[str, str]:
        return {"If-Match": f'"{version}"'}


def sign_in(client: TestClient, email: str) -> Actor:
    """Full magic-link round trip: request a link, then verify its token."""
    requested = client.post(f"{API}/auth/magic-links", json={"email": email})
    assert requested.status_code == 200, requested.text
    token = requested.json()["token"]

    verified = client.post(f"{API}/auth/magic-links/verify", json={"token": token})
    assert verified.status_code == 200, verified.text
    body = verified.json()
    return Actor(
        client=client,
        email=body["user"]["email"],
        user_id=body["user"]["id"],
        token=body["sessionToken"],
    )


@pytest.fixture
def alice(client: TestClient) -> Actor:
    return sign_in(client, "alice@example.com")


@pytest.fixture
def bob(client: TestClient) -> Actor:
    return sign_in(client, "bob@example.com")


@pytest.fixture
def carol(client: TestClient) -> Actor:
    return sign_in(client, "carol@example.com")


@dataclass
class GroupCtx:
    """A created group plus the member ids of everyone who joined it."""

    id: str
    version: int
    invite_code: str
    members: dict[str, str]  # actor email -> member id


def make_group(
    creator: Actor,
    *,
    name: str = "Lisbon Trip",
    currency: str = "EUR",
    display_name: str = "Alice",
) -> GroupCtx:
    response = creator.post(
        "/groups",
        json={"name": name, "currency": currency, "displayName": display_name},
    )
    assert response.status_code == 201, response.text
    group = response.json()
    snapshot = creator.get(f"/groups/{group['id']}").json()
    return GroupCtx(
        id=group["id"],
        version=group["version"],
        invite_code=group["inviteCode"],
        members={creator.email: snapshot["me"]["memberId"]},
    )


def join(group: GroupCtx, actor: Actor, display_name: str) -> str:
    response = actor.post(
        f"/invites/{group.invite_code}/join", json={"displayName": display_name}
    )
    assert response.status_code == 200, response.text
    member_id = response.json()["member"]["id"]
    group.members[actor.email] = member_id
    return member_id


@pytest.fixture
def group(alice: Actor) -> GroupCtx:
    """A group whose only member is Alice."""
    return make_group(alice)


@pytest.fixture
def trio(alice: Actor, bob: Actor, carol: Actor) -> GroupCtx:
    """A group with Alice (creator), Bob, and Carol."""
    ctx = make_group(alice)
    join(ctx, bob, "Bob")
    join(ctx, carol, "Carol")
    return ctx
