"""Unit tests for the pure split / balance / settlement logic.

These exercise edge cases that are awkward to reach through HTTP — very large
member counts, adversarial rounding, and the exact tie-break ordering.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.balances import compute_balances
from app.domain.settle import minimized_transfers, relationship_preserving_transfers
from app.domain.split import compute_allocation
from app.models import Expense, ExpenseShare, Member, Payment

STAMP = datetime(2026, 9, 2, tzinfo=UTC)


def member(member_id: str) -> Member:
    return Member(
        id=member_id,
        group_id="grp_1",
        user_id=f"usr_{member_id}",
        display_name=member_id,
        created_at=STAMP,
        updated_at=STAMP,
    )


def expense(payer: str, amount: int, shares: dict[str, int]) -> Expense:
    return Expense(
        id=f"exp_{payer}_{amount}",
        group_id="grp_1",
        description="x",
        note="",
        amount_minor=amount,
        date=date(2026, 9, 2),
        payer_member_id=payer,
        split_type="exact",
        split_inputs=[],
        shares=[ExpenseShare(m, v) for m, v in shares.items()],
        created_by_user_id="usr_1",
        created_at=STAMP,
        updated_at=STAMP,
    )


def payment(payer: str, recipient: str, amount: int) -> Payment:
    return Payment(
        id=f"pay_{payer}_{recipient}",
        group_id="grp_1",
        payer_member_id=payer,
        recipient_member_id=recipient,
        amount_minor=amount,
        date=date(2026, 9, 4),
        note="",
        created_by_user_id="usr_1",
        created_at=STAMP,
        updated_at=STAMP,
    )


def allocate(split_type: str, amount: int, participants):
    return compute_allocation(
        split_type=split_type, amount_minor=amount, participants=participants
    )


def amounts(result) -> dict[str, int]:
    return {s.member_id: s.amount_minor for s in result.shares}


class TestEqualSplit:
    def test_splits_evenly_when_divisible(self):
        result = allocate("equal", 900, [("a", None), ("b", None), ("c", None)])
        assert result.ok
        assert amounts(result) == {"a": 300, "b": 300, "c": 300}

    def test_remainder_goes_to_the_lowest_ids(self):
        result = allocate("equal", 1000, [("c", None), ("a", None), ("b", None)])
        assert result.ok
        # 333 each, 1 left over -> the lowest id by string order.
        assert amounts(result) == {"a": 334, "b": 333, "c": 333}

    def test_two_leftover_units_go_to_the_two_lowest_ids(self):
        result = allocate("equal", 1001, [("c", None), ("a", None), ("b", None)])
        assert amounts(result) == {"a": 334, "b": 334, "c": 333}

    def test_single_participant_takes_everything(self):
        result = allocate("equal", 8731, [("a", None)])
        assert amounts(result) == {"a": 8731}

    @pytest.mark.parametrize("amount", [1, 2, 3, 99, 100, 101, 9999, 1234567])
    @pytest.mark.parametrize("count", [1, 2, 3, 7, 13, 100])
    def test_always_reconciles(self, amount, count):
        ids = [(f"m{i:03d}", None) for i in range(count)]
        result = allocate("equal", amount, ids)
        assert result.ok
        assert result.allocated_minor == amount
        assert sum(s.amount_minor for s in result.shares) == amount

    def test_shares_differ_by_at_most_one_unit(self):
        result = allocate("equal", 1000, [(f"m{i}", None) for i in range(7)])
        values = [s.amount_minor for s in result.shares]
        assert max(values) - min(values) <= 1


class TestShareSplit:
    def test_weights_the_allocation(self):
        result = allocate("share", 8000, [("a", 1), ("b", 2), ("c", 1)])
        assert amounts(result) == {"a": 2000, "b": 4000, "c": 2000}

    def test_accepts_fractional_weights(self):
        result = allocate("share", 1000, [("a", 0.5), ("b", 1.5)])
        assert result.ok
        assert amounts(result) == {"a": 250, "b": 750}

    def test_accepts_numeric_strings(self):
        result = allocate("share", 300, [("a", "1"), ("b", "2")])
        assert result.ok
        assert amounts(result) == {"a": 100, "b": 200}

    @pytest.mark.parametrize("raw", [0, -1, None, "", "abc", float("nan")])
    def test_rejects_a_non_positive_weight(self, raw):
        result = allocate("share", 1000, [("a", raw), ("b", 1)])
        assert not result.ok
        assert "positive" in result.error

    @pytest.mark.parametrize("amount", [1, 7, 100, 8731, 999983])
    def test_always_reconciles(self, amount):
        result = allocate("share", amount, [("a", 1), ("b", 2), ("c", 3), ("d", 5)])
        assert result.ok
        assert sum(s.amount_minor for s in result.shares) == amount


class TestPercentageSplit:
    def test_exact_percentages(self):
        result = allocate("percentage", 10000, [("a", 50), ("b", 30), ("c", 20)])
        assert result.ok
        assert amounts(result) == {"a": 5000, "b": 3000, "c": 2000}

    def test_two_decimal_places_are_honoured(self):
        result = allocate("percentage", 10000, [("a", 33.33), ("b", 66.67)])
        assert result.ok
        assert amounts(result) == {"a": 3333, "b": 6667}

    def test_totals_below_100_are_rejected(self):
        result = allocate("percentage", 1000, [("a", 50), ("b", 40)])
        assert not result.ok
        assert "90.00%" in result.error

    def test_totals_above_100_are_rejected(self):
        result = allocate("percentage", 1000, [("a", 60), ("b", 50)])
        assert not result.ok
        assert "110.00%" in result.error

    def test_all_zero_is_rejected(self):
        result = allocate("percentage", 1000, [("a", 0), ("b", 0)])
        assert not result.ok

    def test_a_zero_percent_participant_is_allowed(self):
        result = allocate("percentage", 1000, [("a", 100), ("b", 0)])
        assert result.ok
        assert amounts(result) == {"a": 1000, "b": 0}

    @pytest.mark.parametrize("raw", [-1, None, "abc"])
    def test_rejects_an_invalid_percentage(self, raw):
        result = allocate("percentage", 1000, [("a", raw), ("b", 100)])
        assert not result.ok

    @pytest.mark.parametrize("amount", [1, 7, 100, 8731, 999983])
    def test_always_reconciles(self, amount):
        result = allocate(
            "percentage", amount, [("a", 33.33), ("b", 33.33), ("c", 33.34)]
        )
        assert result.ok
        assert sum(s.amount_minor for s in result.shares) == amount


class TestExactSplit:
    def test_takes_the_values_verbatim(self):
        result = allocate("exact", 1000, [("a", 500), ("b", 300), ("c", 200)])
        assert result.ok
        assert amounts(result) == {"a": 500, "b": 300, "c": 200}

    def test_rejects_an_under_allocation(self):
        result = allocate("exact", 1000, [("a", 400), ("b", 400)])
        assert not result.ok
        assert "reconcile" in result.error

    def test_rejects_an_over_allocation(self):
        result = allocate("exact", 1000, [("a", 600), ("b", 600)])
        assert not result.ok

    def test_rejects_a_negative_amount(self):
        result = allocate("exact", 1000, [("a", 1100), ("b", -100)])
        assert not result.ok

    def test_rounds_fractional_input_to_minor_units(self):
        result = allocate("exact", 1000, [("a", 500.4), ("b", 499.6)])
        assert result.ok
        assert amounts(result) == {"a": 500, "b": 500}


class TestAllocationGuards:
    def test_no_participants(self):
        result = allocate("equal", 1000, [])
        assert not result.ok
        assert "participant" in result.error

    @pytest.mark.parametrize("amount", [0, -1])
    def test_non_positive_amount(self, amount):
        result = allocate("equal", amount, [("a", None)])
        assert not result.ok
        assert "greater than zero" in result.error

    def test_unknown_split_type(self):
        result = allocate("magic", 1000, [("a", None)])
        assert not result.ok
        assert "magic" in result.error


class TestBalances:
    def test_payer_is_credited_and_participants_debited(self):
        members = [member("a"), member("b")]
        result = compute_balances(
            members, [expense("a", 1000, {"a": 500, "b": 500})], []
        )
        assert result == [
            {"memberId": "a", "netMinor": 500},
            {"memberId": "b", "netMinor": -500},
        ]

    def test_a_payment_moves_both_sides_toward_zero(self):
        members = [member("a"), member("b")]
        result = compute_balances(
            members,
            [expense("a", 1000, {"a": 500, "b": 500})],
            [payment("b", "a", 500)],
        )
        assert {r["netMinor"] for r in result} == {0}

    def test_members_with_no_activity_are_zero(self):
        result = compute_balances([member("a"), member("b")], [], [])
        assert {r["netMinor"] for r in result} == {0}

    def test_always_sums_to_zero(self):
        members = [member(x) for x in "abc"]
        expenses = [
            expense("a", 1000, {"a": 334, "b": 333, "c": 333}),
            expense("b", 777, {"b": 389, "c": 388}),
        ]
        result = compute_balances(members, expenses, [payment("c", "a", 100)])
        assert sum(r["netMinor"] for r in result) == 0

    def test_preserves_member_order(self):
        members = [member("c"), member("a"), member("b")]
        result = compute_balances(members, [], [])
        assert [r["memberId"] for r in result] == ["c", "a", "b"]


class TestMinimizedTransfers:
    def test_empty_when_everyone_is_settled(self):
        assert minimized_transfers([{"memberId": "a", "netMinor": 0}]) == []

    def test_spec_example(self):
        balances = [
            {"memberId": "alice", "netMinor": 6000},
            {"memberId": "bob", "netMinor": -4000},
            {"memberId": "carol", "netMinor": -2000},
        ]
        assert minimized_transfers(balances) == [
            {"fromMemberId": "bob", "toMemberId": "alice", "amountMinor": 4000},
            {"fromMemberId": "carol", "toMemberId": "alice", "amountMinor": 2000},
        ]

    def test_uses_at_most_n_minus_one_transfers(self):
        balances = [
            {"memberId": "a", "netMinor": 500},
            {"memberId": "b", "netMinor": 300},
            {"memberId": "c", "netMinor": -400},
            {"memberId": "d", "netMinor": -400},
        ]
        assert len(minimized_transfers(balances)) <= 3

    def test_transfers_clear_every_balance(self):
        balances = [
            {"memberId": "a", "netMinor": 701},
            {"memberId": "b", "netMinor": -250},
            {"memberId": "c", "netMinor": -1},
            {"memberId": "d", "netMinor": -450},
        ]
        net = {b["memberId"]: b["netMinor"] for b in balances}
        for t in minimized_transfers(balances):
            assert t["amountMinor"] > 0
            net[t["fromMemberId"]] += t["amountMinor"]
            net[t["toMemberId"]] -= t["amountMinor"]
        assert set(net.values()) == {0}

    def test_does_not_mutate_the_input(self):
        balances = [
            {"memberId": "a", "netMinor": 100},
            {"memberId": "b", "netMinor": -100},
        ]
        minimized_transfers(balances)
        assert balances[0]["netMinor"] == 100
        assert balances[1]["netMinor"] == -100

    def test_is_deterministic_regardless_of_input_order(self):
        forward = [
            {"memberId": "a", "netMinor": 300},
            {"memberId": "b", "netMinor": 300},
            {"memberId": "c", "netMinor": -600},
        ]
        assert minimized_transfers(forward) == minimized_transfers(forward[::-1])


class TestRelationshipTransfers:
    def test_keeps_the_original_pairs(self):
        members = [member(x) for x in "abc"]
        expenses = [
            expense("b", 2000, {"b": 1000, "c": 1000}),
            expense("a", 2000, {"a": 1000, "b": 1000}),
        ]
        assert relationship_preserving_transfers(members, expenses, []) == [
            {"fromMemberId": "b", "toMemberId": "a", "amountMinor": 1000},
            {"fromMemberId": "c", "toMemberId": "b", "amountMinor": 1000},
        ]

    def test_nets_mutual_debt(self):
        members = [member("a"), member("b")]
        expenses = [
            expense("a", 3000, {"a": 1500, "b": 1500}),
            expense("b", 1000, {"a": 500, "b": 500}),
        ]
        assert relationship_preserving_transfers(members, expenses, []) == [
            {"fromMemberId": "b", "toMemberId": "a", "amountMinor": 1000}
        ]

    def test_payments_cancel_the_debt(self):
        members = [member("a"), member("b")]
        expenses = [expense("a", 3000, {"a": 1500, "b": 1500})]
        assert (
            relationship_preserving_transfers(members, expenses, [payment("b", "a", 1500)])
            == []
        )

    def test_overpayment_reverses_the_direction(self):
        members = [member("a"), member("b")]
        expenses = [expense("a", 3000, {"a": 1500, "b": 1500})]
        assert relationship_preserving_transfers(
            members, expenses, [payment("b", "a", 2000)]
        ) == [{"fromMemberId": "a", "toMemberId": "b", "amountMinor": 500}]

    def test_the_payer_never_owes_their_own_share(self):
        members = [member("a"), member("b")]
        expenses = [expense("a", 1000, {"a": 1000})]
        assert relationship_preserving_transfers(members, expenses, []) == []

    def test_is_sorted_by_debtor_then_creditor(self):
        members = [member(x) for x in "abcd"]
        expenses = [
            expense("d", 400, {"a": 200, "b": 200}),
            expense("c", 200, {"a": 200}),
        ]
        result = relationship_preserving_transfers(members, expenses, [])
        pairs = [(t["fromMemberId"], t["toMemberId"]) for t in result]
        assert pairs == sorted(pairs)

    def test_empty_group(self):
        assert relationship_preserving_transfers([], [], []) == []
