"""Shared fixtures.

Every test gets a clean database and a small `Actor` helper that wraps the
TestClient with a session token, so tests read as "alice does X" rather than as
header plumbing.

The database is a real Postgres, by default the `db` service from
`compose.yaml` (`docker compose up -d db`). Tests use their own database on it,
`viloq_test`, created on first use and emptied after every test; point
`VILOQ_TEST_DATABASE_URL` elsewhere to override.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

TEST_DATABASE_URL = os.environ.get(
    "VILOQ_TEST_DATABASE_URL",
    "postgresql+psycopg://viloq:viloq@localhost:5432/viloq_test",
)

# The app's startup hook creates the schema in whatever `VILOQ_DATABASE_URL`
# names, whether or not the tests override `get_db`. Point it at the test
# database before importing it, so a test run never touches the dev data.
os.environ["VILOQ_DATABASE_URL"] = TEST_DATABASE_URL

from backend.db import Database, create_db_engine, get_db, new_session_factory  # noqa: E402
from backend.main import app as fastapi_app  # noqa: E402
from backend.models import Base  # noqa: E402

API = "/api/v1"


def as_datetime(iso: str) -> datetime:
    """Parse an ISO-8601 timestamp from a wire body for chronological comparison.

    Comparing the raw strings is wrong at the fractional-seconds boundary: a
    timestamp whose microseconds are exactly zero serializes without a fraction
    (`...T12:00:00Z`), and `Z` sorts after `.`, so a strictly later `updatedAt`
    can compare as less than `createdAt`.
    """
    return datetime.fromisoformat(iso)


def _ensure_database_exists(url: str) -> None:
    """Create the test database on its server if it is not there yet."""
    target = make_url(url)
    admin = create_engine(
        target.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                connection.exec_driver_sql(f'CREATE DATABASE "{target.database}"')
    finally:
        admin.dispose()


def empty_all_tables(engine: Engine) -> None:
    """Delete every row the test left behind, keeping the schema."""
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"TRUNCATE {tables} CASCADE")


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    """The test database, with the schema in place. Shared by the whole run."""
    name = make_url(TEST_DATABASE_URL).database or ""
    if not name.endswith("_test"):
        # Every test truncates every table: never let that loose on real data.
        pytest.exit(
            f"Refusing to run against database {name!r}: the test database's "
            "name must end in '_test'.",
            returncode=1,
        )
    try:
        _ensure_database_exists(TEST_DATABASE_URL)
    except OperationalError as exc:
        pytest.exit(
            "Cannot reach Postgres for the tests. Start it with "
            "`docker compose up -d db`, or set VILOQ_TEST_DATABASE_URL.\n"
            f"{exc.orig}",
            returncode=1,
        )
    engine = create_db_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(engine)  # whatever shape an earlier run left
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(test_engine: Engine) -> Iterator[Database]:
    try:
        with new_session_factory(test_engine)() as session:
            yield Database(session)
    finally:
        # After the session closes: an open transaction would hold locks that
        # TRUNCATE has to wait for.
        empty_all_tables(test_engine)


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
