"""The running stack from `compose.yaml`, driven over real HTTP.

Everything else in `tests/` runs the app in-process through `TestClient`. These
talk to a deployed one instead — the built image, uvicorn, the static frontend
files baked into it, and its own Postgres — so they catch what only exists
once it is all wired together.

They are skipped unless `VILOQ_E2E_BASE_URL` names the stack:

    docker compose up -d --build --wait
    VILOQ_E2E_BASE_URL=http://localhost:8000 uv run pytest tests/integration

The stack's database outlives a run, so every test signs in with addresses no
earlier run used, and asserts only about what it created.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

BASE_URL = os.environ.get("VILOQ_E2E_BASE_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not BASE_URL, reason="set VILOQ_E2E_BASE_URL to run against a running stack"
)

API = "/api/v1"


@pytest.fixture(scope="module")
def http() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        try:
            client.get("/")
        except httpx.TransportError as exc:
            pytest.fail(f"No stack at {BASE_URL}: {exc}")
        yield client


@dataclass
class User:
    http: httpx.Client
    email: str
    token: str

    def request(self, method: str, path: str, version: int | None = None, **kw: Any):
        headers = {"Authorization": f"Bearer {self.token}", **kw.pop("headers", {})}
        if version is not None:
            headers["If-Match"] = f'"{version}"'
        return self.http.request(method, f"{API}{path}", headers=headers, **kw)


def sign_in(http: httpx.Client, name: str) -> User:
    """The magic-link round trip, as a fresh address no other run has used."""
    email = f"{name}-{uuid.uuid4().hex[:12]}@example.com"
    link = http.post(f"{API}/auth/magic-links", json={"email": email})
    assert link.status_code == 200, link.text
    verified = http.post(
        f"{API}/auth/magic-links/verify", json={"token": link.json()["token"]}
    )
    assert verified.status_code == 200, verified.text
    return User(http, email, verified.json()["sessionToken"])


@dataclass
class Trio:
    """A group Alice created, which Bob and Carol joined by invite."""

    id: str
    alice: User
    bob: User
    carol: User
    members: dict[str, str] = field(default_factory=dict)  # name -> member id


@pytest.fixture
def trio(http: httpx.Client) -> Trio:
    alice, bob, carol = (sign_in(http, n) for n in ("alice", "bob", "carol"))
    created = alice.request(
        "POST",
        "/groups",
        json={"name": "Lisbon trip", "currency": "EUR", "displayName": "Alice"},
    )
    assert created.status_code == 201, created.text
    group = created.json()
    for user, name in ((bob, "Bob"), (carol, "Carol")):
        joined = user.request(
            "POST", f"/invites/{group['inviteCode']}/join", json={"displayName": name}
        )
        assert joined.status_code == 200, joined.text

    snapshot = alice.request("GET", f"/groups/{group['id']}").json()
    members = {m["displayName"]: m["id"] for m in snapshot["members"]}
    return Trio(group["id"], alice, bob, carol, members)


def expense(trio: Trio, payer: str, amount: int, **overrides) -> dict:
    """An equal three-way split of `amount`, paid by `payer`."""
    body = {
        "description": "Dinner",
        "date": "2026-09-30",
        "amountMinor": amount,
        "payerMemberId": trio.members[payer],
        "splitType": "equal",
        "participants": [{"memberId": m} for m in trio.members.values()],
    }
    body.update(overrides)
    return body


class TestFrontend:
    def test_the_page_is_served_at_the_root(self, http: httpx.Client):
        response = http.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert 'id="app"' in response.text

    @pytest.mark.parametrize(
        "path",
        [
            "/styles.css",
            "/js/app.js",
            "/js/api.js",
            "/js/views/dashboard.js",
            "/js/lib/settle.js",
        ],
    )
    def test_the_assets_made_it_into_the_image(self, http: httpx.Client, path: str):
        assert http.get(path).status_code == 200

    def test_modules_are_served_as_javascript(self, http: httpx.Client):
        # Browsers refuse to run `<script type="module">` under any other type.
        assert "javascript" in http.get("/js/app.js").headers["content-type"]

    def test_an_unknown_file_is_a_contract_shaped_404(self, http: httpx.Client):
        response = http.get("/nope.js")
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    def test_the_api_docs_are_reachable(self, http: httpx.Client):
        assert http.get(f"{API}/docs").status_code == 200
        assert http.get(f"{API}/openapi.json").status_code == 200


class TestAuth:
    def test_no_session_is_401(self, http: httpx.Client):
        response = http.get(f"{API}/auth/me")
        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"

    def test_a_bad_email_is_a_validation_error(self, http: httpx.Client):
        response = http.post(f"{API}/auth/magic-links", json={"email": "not-an-email"})
        assert response.status_code == 400
        assert response.json()["code"] == "validation"

    def test_a_magic_link_signs_in(self, http: httpx.Client):
        user = sign_in(http, "alice")
        me = user.request("GET", "/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == user.email


class TestGroupLifecycle:
    def test_invite_info_names_the_group(self, http: httpx.Client, trio: Trio):
        group = trio.alice.request("GET", f"/groups/{trio.id}").json()["group"]
        outsider = sign_in(http, "dave")
        info = outsider.request("GET", f"/invites/{group['inviteCode']}")
        assert info.status_code == 200
        assert info.json()["groupName"] == "Lisbon trip"

    def test_everyone_who_joined_is_a_member(self, trio: Trio):
        assert set(trio.members) == {"Alice", "Bob", "Carol"}

    def test_an_equal_split_divides_the_total(self, trio: Trio):
        response = trio.alice.request(
            "POST", f"/groups/{trio.id}/expenses", json=expense(trio, "Alice", 9000)
        )
        assert response.status_code == 201, response.text
        assert sorted(s["amountMinor"] for s in response.json()["shares"]) == [3000] * 3

    def test_an_uneven_split_still_reconciles_to_the_total(self, trio: Trio):
        response = trio.bob.request(
            "POST", f"/groups/{trio.id}/expenses", json=expense(trio, "Bob", 1000)
        )
        assert response.status_code == 201, response.text
        assert sum(s["amountMinor"] for s in response.json()["shares"]) == 1000

    def test_percentages_must_total_100(self, trio: Trio):
        body = expense(
            trio,
            "Alice",
            1000,
            splitType="percentage",
            participants=[
                {"memberId": trio.members["Alice"], "raw": 50},
                {"memberId": trio.members["Bob"], "raw": 20},
            ],
        )
        response = trio.alice.request("POST", f"/groups/{trio.id}/expenses", json=body)
        assert response.status_code == 400

    def test_an_amount_past_32_bits_is_stored(self, trio: Trio):
        big = 3 * 2**31
        response = trio.alice.request(
            "POST", f"/groups/{trio.id}/expenses", json=expense(trio, "Alice", big)
        )
        assert response.status_code == 201, response.text
        assert response.json()["amountMinor"] == big

    def test_a_stale_version_is_a_conflict(self, trio: Trio):
        created = trio.alice.request(
            "POST", f"/groups/{trio.id}/expenses", json=expense(trio, "Alice", 9000)
        ).json()
        path = f"/groups/{trio.id}/expenses/{created['id']}"
        edit = expense(trio, "Alice", 9000, description="Dinner (edited)")

        first = trio.alice.request("PUT", path, version=created["version"], json=edit)
        assert first.status_code == 200, first.text
        assert first.json()["version"] == created["version"] + 1

        stale = trio.bob.request("PUT", path, version=created["version"], json=edit)
        assert stale.status_code == 409
        assert stale.json()["code"] == "version_conflict"

    def test_settlement_reflects_expenses_and_payments(self, trio: Trio):
        for payer, amount in (("Alice", 9000), ("Bob", 1000)):
            created = trio.alice.request(
                "POST", f"/groups/{trio.id}/expenses", json=expense(trio, payer, amount)
            )
            assert created.status_code == 201, created.text
        paid = trio.carol.request(
            "POST",
            f"/groups/{trio.id}/payments",
            json={
                "recipientMemberId": trio.members["Alice"],
                "amountMinor": 2000,
                "date": "2026-10-01",
            },
        )
        assert paid.status_code == 201, paid.text

        response = trio.alice.request("GET", f"/groups/{trio.id}/settlement")
        assert response.status_code == 200
        transfers = {
            (t["fromMemberId"], t["toMemberId"]): t["amountMinor"]
            for t in response.json()["transfers"]
        }
        alice, bob, carol = (trio.members[n] for n in ("Alice", "Bob", "Carol"))
        # Shares of 9000 are 3000 each; of 1000, one of 334 and two of 333.
        # Bob owes 3000 + 333 - 1000; Carol owes 3333 less the 2000 she paid.
        # Alice may hold either 1000-split share, so allow for the extra cent.
        assert set(transfers) == {(bob, alice), (carol, alice)}
        assert transfers[(bob, alice)] in (2333, 2334)
        assert transfers[(carol, alice)] in (1333, 1334)
        assert transfers[(bob, alice)] + transfers[(carol, alice)] in (3666, 3667)

    def test_a_non_member_cannot_read_the_group(self, http: httpx.Client, trio: Trio):
        outsider = sign_in(http, "mallory")
        assert outsider.request("GET", f"/groups/{trio.id}").status_code in (403, 404)
