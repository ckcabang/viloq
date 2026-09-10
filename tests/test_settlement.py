"""GET /groups/{id}/settlement, and the balances that feed it."""

from __future__ import annotations

from tests.conftest import API, Actor, GroupCtx


def expense(actor: Actor, group: GroupCtx, payer: Actor, amount: int, *among: Actor):
    """An equal-split expense paid by `payer` and shared among `among`."""
    return actor.post(
        f"/groups/{group.id}/expenses",
        json={
            "description": "Shared",
            "date": "2026-09-02",
            "amountMinor": amount,
            "payerMemberId": group.members[payer.email],
            "splitType": "equal",
            "participants": [{"memberId": group.members[a.email]} for a in among],
        },
    )


def transfers(actor: Actor, group: GroupCtx, strategy: str | None = None):
    params = {"strategy": strategy} if strategy else None
    response = actor.get(f"/groups/{group.id}/settlement", params=params)
    assert response.status_code == 200, response.text
    return response.json()


class TestBalances:
    def test_everyone_starts_settled(self, alice: Actor, trio: GroupCtx):
        balances = alice.get(f"/groups/{trio.id}").json()["balances"]
        assert {b["netMinor"] for b in balances} == {0}

    def test_balances_always_sum_to_zero(
        self, alice: Actor, bob: Actor, carol: Actor, trio: GroupCtx
    ):
        expense(alice, trio, alice, 9001, alice, bob, carol)
        expense(bob, trio, bob, 5000, bob, carol)
        balances = alice.get(f"/groups/{trio.id}").json()["balances"]
        assert sum(b["netMinor"] for b in balances) == 0

    def test_payer_is_owed_the_others_shares(
        self, alice: Actor, bob: Actor, carol: Actor, trio: GroupCtx
    ):
        expense(alice, trio, alice, 9000, alice, bob, carol)
        balances = {
            b["memberId"]: b["netMinor"]
            for b in alice.get(f"/groups/{trio.id}").json()["balances"]
        }
        assert balances[trio.members[alice.email]] == 6000
        assert balances[trio.members[bob.email]] == -3000
        assert balances[trio.members[carol.email]] == -3000


class TestMinimizedStrategy:
    def test_defaults_to_minimized(self, alice: Actor, trio: GroupCtx):
        assert transfers(alice, trio)["strategy"] == "minimized"

    def test_no_transfers_when_settled(self, alice: Actor, trio: GroupCtx):
        assert transfers(alice, trio)["transfers"] == []

    def test_matches_the_spec_example(
        self, alice: Actor, bob: Actor, carol: Actor, trio: GroupCtx
    ):
        # The spec's section 9 example: Alice +60, Bob -40, Carol -20.
        alice.post(
            f"/groups/{trio.id}/expenses",
            json={
                "description": "Trip",
                "date": "2026-09-02",
                "amountMinor": 6000,
                "payerMemberId": trio.members[alice.email],
                "splitType": "exact",
                "participants": [
                    {"memberId": trio.members[alice.email], "raw": 0},
                    {"memberId": trio.members[bob.email], "raw": 4000},
                    {"memberId": trio.members[carol.email], "raw": 2000},
                ],
            },
        )

        result = transfers(alice, trio, "minimized")
        assert result["strategy"] == "minimized"
        assert result["transfers"] == [
            {
                "fromMemberId": trio.members[bob.email],
                "toMemberId": trio.members[alice.email],
                "amountMinor": 4000,
            },
            {
                "fromMemberId": trio.members[carol.email],
                "toMemberId": trio.members[alice.email],
                "amountMinor": 2000,
            },
        ]

    def test_uses_at_most_n_minus_one_transfers(
        self, alice: Actor, bob: Actor, carol: Actor, trio: GroupCtx
    ):
        expense(alice, trio, alice, 9000, alice, bob, carol)
        expense(bob, trio, bob, 3000, alice, bob, carol)
        result = transfers(alice, trio, "minimized")
        assert len(result["transfers"]) <= 2

    def test_transfers_clear_every_balance(
        self, alice: Actor, bob: Actor, carol: Actor, trio: GroupCtx
    ):
        expense(alice, trio, alice, 9001, alice, bob, carol)
        expense(bob, trio, bob, 5000, bob, carol)

        balances = {
            b["memberId"]: b["netMinor"]
            for b in alice.get(f"/groups/{trio.id}").json()["balances"]
        }
        for t in transfers(alice, trio, "minimized")["transfers"]:
            assert t["amountMinor"] > 0
            balances[t["fromMemberId"]] += t["amountMinor"]
            balances[t["toMemberId"]] -= t["amountMinor"]
        assert set(balances.values()) == {0}

    def test_recorded_payments_shrink_the_suggestion(
        self, alice: Actor, bob: Actor, carol: Actor, trio: GroupCtx
    ):
        expense(alice, trio, alice, 9000, alice, bob, carol)
        bob.post(
            f"/groups/{trio.id}/payments",
            json={
                "recipientMemberId": trio.members[alice.email],
                "amountMinor": 3000,
                "date": "2026-09-05",
            },
        )
        result = transfers(alice, trio, "minimized")
        assert result["transfers"] == [
            {
                "fromMemberId": trio.members[carol.email],
                "toMemberId": trio.members[alice.email],
                "amountMinor": 3000,
            }
        ]


class TestRelationshipStrategy:
    def test_echoes_the_strategy(self, alice: Actor, trio: GroupCtx):
        assert transfers(alice, trio, "relationship")["strategy"] == "relationship"

    def test_keeps_the_real_debtor_creditor_pairs(
        self, alice: Actor, bob: Actor, carol: Actor, trio: GroupCtx
    ):
        # Bob paid for Carol; Alice paid for Bob. Minimized would route
        # Carol -> Alice, but relationship keeps Carol -> Bob and Bob -> Alice.
        expense(bob, trio, bob, 2000, bob, carol)
        expense(alice, trio, alice, 2000, alice, bob)

        result = transfers(alice, trio, "relationship")
        pairs = {
            (t["fromMemberId"], t["toMemberId"]): t["amountMinor"]
            for t in result["transfers"]
        }
        assert pairs == {
            (trio.members[carol.email], trio.members[bob.email]): 1000,
            (trio.members[bob.email], trio.members[alice.email]): 1000,
        }

    def test_nets_mutual_debt_between_two_members(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        expense(alice, trio, alice, 3000, alice, bob)  # Bob owes Alice 1500
        expense(bob, trio, bob, 1000, alice, bob)  # Alice owes Bob 500

        result = transfers(alice, trio, "relationship")
        assert result["transfers"] == [
            {
                "fromMemberId": trio.members[bob.email],
                "toMemberId": trio.members[alice.email],
                "amountMinor": 1000,
            }
        ]

    def test_applies_recorded_payments(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        expense(alice, trio, alice, 3000, alice, bob)  # Bob owes Alice 1500
        bob.post(
            f"/groups/{trio.id}/payments",
            json={
                "recipientMemberId": trio.members[alice.email],
                "amountMinor": 1500,
                "date": "2026-09-05",
            },
        )
        assert transfers(alice, trio, "relationship")["transfers"] == []

    def test_is_empty_when_settled(self, alice: Actor, trio: GroupCtx):
        assert transfers(alice, trio, "relationship")["transfers"] == []


class TestSettlementAccess:
    def test_unrecognized_strategy_is_400(self, alice: Actor, trio: GroupCtx):
        response = alice.get(
            f"/groups/{trio.id}/settlement", params={"strategy": "cheapest"}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "validation"

    def test_never_creates_payment_records(self, alice: Actor, bob: Actor, trio):
        expense(alice, trio, alice, 9000, alice, bob)
        transfers(alice, trio, "minimized")
        transfers(alice, trio, "relationship")
        assert alice.get(f"/groups/{trio.id}").json()["payments"] == []

    def test_non_member_is_403(self, bob: Actor, group: GroupCtx):
        response = bob.get(f"/groups/{group.id}/settlement")
        assert response.status_code == 403

    def test_unknown_group_is_404(self, alice: Actor):
        assert alice.get("/groups/grp_missing/settlement").status_code == 404

    def test_requires_a_session(self, client, trio: GroupCtx):
        assert client.get(f"{API}/groups/{trio.id}/settlement").status_code == 401
