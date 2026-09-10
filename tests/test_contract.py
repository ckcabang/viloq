"""Cross-cutting checks that the app matches `openapi.yaml`.

The contract is the source of truth, so these tests read it directly rather than
restating it: if an operation is added to the spec and not to the app (or vice
versa), the suite fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import API, Actor, GroupCtx

SPEC = Path(__file__).resolve().parents[1] / "openapi.yaml"


def spec_operations() -> set[tuple[str, str]]:
    """(method, path) pairs declared in openapi.yaml, without a YAML parser.

    Paths sit at one indent level under `paths:`; methods sit one level under a
    path. That is enough structure to enumerate the contract's operations.
    """
    methods = {"get", "post", "put", "patch", "delete"}
    operations: set[tuple[str, str]] = set()
    current: str | None = None
    in_paths = False

    for line in SPEC.read_text(encoding="utf-8").splitlines():
        if line.startswith("paths:"):
            in_paths = True
            continue
        if not in_paths:
            continue
        if line and not line[0].isspace():
            break  # left the paths section (e.g. `components:`)

        path_match = re.match(r"^  (/[^:]*):\s*$", line)
        if path_match:
            current = path_match.group(1)
            continue
        method_match = re.match(r"^    ([a-z]+):\s*$", line)
        if method_match and current and method_match.group(1) in methods:
            operations.add((method_match.group(1).upper(), current))

    return operations


@pytest.fixture(scope="module")
def declared() -> set[tuple[str, str]]:
    operations = spec_operations()
    assert operations, "no operations parsed out of openapi.yaml"
    return operations


def implemented(app) -> set[tuple[str, str]]:
    """(method, path) pairs the app actually serves, from its own schema."""
    found: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(API):
            continue
        for method in operations:
            found.add((method.upper(), path[len(API) :]))
    return found


class TestContractCoverage:
    def test_every_declared_operation_is_implemented(self, app, declared):
        assert not declared - implemented(app)

    def test_no_undeclared_operations_are_exposed(self, app, declared):
        assert not implemented(app) - declared

    def test_the_generated_schema_is_servable(self, client):
        response = client.get(f"{API}/openapi.json")
        assert response.status_code == 200
        assert response.json()["info"]["title"] == "viloq API"


class TestErrorShape:
    """Every error the contract can produce is `{code, message}`."""

    def cases(self, alice: Actor, group: GroupCtx):
        return [
            (400, alice.post("/groups", json={"name": "", "currency": "EUR", "displayName": "A"})),
            (401, alice.client.get(f"{API}/auth/me")),
            (404, alice.get("/groups/grp_missing")),
            (412, alice.patch(f"/groups/{group.id}", json={"name": "X"})),
        ]

    def test_error_bodies_have_exactly_code_and_message(
        self, alice: Actor, group: GroupCtx
    ):
        for expected_status, response in self.cases(alice, group):
            assert response.status_code == expected_status, response.text
            body = response.json()
            assert set(body) == {"code", "message"}, body
            assert isinstance(body["code"], str) and body["code"]
            assert isinstance(body["message"], str) and body["message"]

    def test_forbidden_body(self, bob: Actor, group: GroupCtx):
        body = bob.get(f"/groups/{group.id}").json()
        assert set(body) == {"code", "message"}
        assert body["code"] == "forbidden"

    def test_version_conflict_adds_only_the_current_record(
        self, alice: Actor, group: GroupCtx
    ):
        alice.patch(
            f"/groups/{group.id}", json={"name": "A"}, headers=alice.if_match(1)
        )
        response = alice.patch(
            f"/groups/{group.id}", json={"name": "B"}, headers=alice.if_match(1)
        )
        assert response.status_code == 409
        body = response.json()
        assert set(body) == {"code", "message", "current"}
        assert body["code"] == "version_conflict"

    def test_unknown_route_uses_the_contract_error_shape(self, client):
        response = client.get(f"{API}/nope")
        assert response.status_code == 404
        assert set(response.json()) == {"code", "message"}
        assert response.json()["code"] == "not_found"

    def test_no_endpoint_returns_a_422(self, alice: Actor, group: GroupCtx):
        responses = [
            alice.post("/groups", json={}),
            alice.post(f"/groups/{group.id}/expenses", json={"description": "x"}),
            alice.post(f"/groups/{group.id}/payments", json={}),
            alice.get(f"/groups/{group.id}/settlement", params={"strategy": "nope"}),
        ]
        assert [r.status_code for r in responses] == [400, 400, 400, 400]


class TestConcurrencyHeaders:
    """Every versioned mutation advertises and enforces its version."""

    def test_create_responses_carry_an_etag(self, alice: Actor, bob: Actor, trio):
        me = trio.members[alice.email]
        group = alice.post(
            "/groups", json={"name": "T", "currency": "USD", "displayName": "A"}
        )
        expense = alice.post(
            f"/groups/{trio.id}/expenses",
            json={
                "description": "x",
                "date": "2026-09-02",
                "amountMinor": 100,
                "payerMemberId": me,
                "splitType": "equal",
                "participants": [{"memberId": me}],
            },
        )
        payment = alice.post(
            f"/groups/{trio.id}/payments",
            json={
                "recipientMemberId": trio.members[bob.email],
                "amountMinor": 100,
                "date": "2026-09-02",
            },
        )
        for response in (group, expense, payment):
            assert response.status_code == 201, response.text
            assert response.headers["ETag"] == '"1"'

    def test_the_etag_is_accepted_back_as_if_match(
        self, alice: Actor, group: GroupCtx
    ):
        created = alice.post(
            "/groups", json={"name": "T", "currency": "USD", "displayName": "A"}
        )
        etag = created.headers["ETag"]
        response = alice.patch(
            f"/groups/{created.json()['id']}",
            json={"name": "Renamed"},
            headers={"If-Match": etag},
        )
        assert response.status_code == 200
        assert response.headers["ETag"] == '"2"'
