"""POST /groups/{id}/expenses, PUT+DELETE /groups/{id}/expenses/{expenseId}."""

from __future__ import annotations

import pytest

from tests.conftest import API, Actor, GroupCtx


def payload(group: GroupCtx, payer: Actor, **overrides) -> dict:
    """An equal 3-way split of 90.00, paid by `payer`."""
    body = {
        "description": "Dinner",
        "date": "2026-09-02",
        "amountMinor": 9000,
        "payerMemberId": group.members[payer.email],
        "splitType": "equal",
        "participants": [{"memberId": m} for m in group.members.values()],
    }
    body.update(overrides)
    return body


def create(actor: Actor, group: GroupCtx, **overrides):
    return actor.post(f"/groups/{group.id}/expenses", json=payload(group, actor, **overrides))


def share_map(expense: dict) -> dict[str, int]:
    return {s["memberId"]: s["amountMinor"] for s in expense["shares"]}


class TestCreateExpense:
    def test_returns_the_resolved_expense_and_an_etag(
        self, alice: Actor, trio: GroupCtx
    ):
        response = create(alice, trio)
        assert response.status_code == 201
        body = response.json()
        assert body["groupId"] == trio.id
        assert body["description"] == "Dinner"
        assert body["date"] == "2026-09-02"
        assert body["amountMinor"] == 9000
        assert body["note"] == ""
        assert body["splitType"] == "equal"
        assert body["createdByUserId"] == alice.user_id
        assert body["version"] == 1
        assert response.headers["ETag"] == '"1"'
        assert sorted(share_map(body).values()) == [3000, 3000, 3000]

    def test_trims_description_and_note(self, alice: Actor, trio: GroupCtx):
        body = create(
            alice, trio, description="  Dinner  ", note="  split evenly  "
        ).json()
        assert body["description"] == "Dinner"
        assert body["note"] == "split evenly"

    def test_any_member_may_add_an_expense_for_any_payer(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        response = bob.post(
            f"/groups/{trio.id}/expenses",
            json=payload(trio, alice),  # Bob records an expense Alice paid
        )
        assert response.status_code == 201
        assert response.json()["payerMemberId"] == trio.members[alice.email]
        assert response.json()["createdByUserId"] == bob.user_id

    def test_accepts_a_subset_of_members(self, alice: Actor, bob: Actor, trio: GroupCtx):
        body = create(
            alice,
            trio,
            amountMinor=1000,
            participants=[
                {"memberId": trio.members[alice.email]},
                {"memberId": trio.members[bob.email]},
            ],
        ).json()
        assert share_map(body) == {
            trio.members[alice.email]: 500,
            trio.members[bob.email]: 500,
        }

    def test_drops_participants_who_are_not_members(
        self, alice: Actor, trio: GroupCtx
    ):
        body = create(
            alice,
            trio,
            amountMinor=1000,
            participants=[
                {"memberId": trio.members[alice.email]},
                {"memberId": "mbr_ghost"},
            ],
        ).json()
        assert share_map(body) == {trio.members[alice.email]: 1000}
        assert [s["memberId"] for s in body["splitInputs"]] == [
            trio.members[alice.email]
        ]

    def test_stores_split_inputs_for_the_edit_form(self, alice: Actor, trio: GroupCtx):
        ids = list(trio.members.values())
        body = create(
            alice,
            trio,
            amountMinor=1000,
            splitType="percentage",
            participants=[
                {"memberId": ids[0], "raw": 50},
                {"memberId": ids[1], "raw": 30},
                {"memberId": ids[2], "raw": 20},
            ],
        ).json()
        assert body["splitInputs"] == [
            {"memberId": ids[0], "raw": 50.0},
            {"memberId": ids[1], "raw": 30.0},
            {"memberId": ids[2], "raw": 20.0},
        ]


class TestSplitResolution:
    def test_equal_split_distributes_the_remainder_deterministically(
        self, alice: Actor, trio: GroupCtx
    ):
        body = create(alice, trio, amountMinor=1000).json()
        shares = share_map(body)
        assert sum(shares.values()) == 1000
        assert sorted(shares.values()) == [333, 333, 334]
        # The extra minor unit goes to the lowest member id.
        assert shares[min(shares)] == 334

    def test_share_split_weights_the_allocation(self, alice: Actor, trio: GroupCtx):
        ids = list(trio.members.values())
        body = create(
            alice,
            trio,
            amountMinor=8000,
            splitType="share",
            participants=[
                {"memberId": ids[0], "raw": 1},
                {"memberId": ids[1], "raw": 2},
                {"memberId": ids[2], "raw": 1},
            ],
        ).json()
        assert share_map(body) == {ids[0]: 2000, ids[1]: 4000, ids[2]: 2000}

    def test_percentage_split_totalling_100_is_accepted(
        self, alice: Actor, trio: GroupCtx
    ):
        ids = list(trio.members.values())
        body = create(
            alice,
            trio,
            amountMinor=10000,
            splitType="percentage",
            participants=[
                {"memberId": ids[0], "raw": 50},
                {"memberId": ids[1], "raw": 25.5},
                {"memberId": ids[2], "raw": 24.5},
            ],
        ).json()
        assert share_map(body) == {ids[0]: 5000, ids[1]: 2550, ids[2]: 2450}

    def test_exact_split_is_taken_verbatim(self, alice: Actor, trio: GroupCtx):
        ids = list(trio.members.values())
        body = create(
            alice,
            trio,
            amountMinor=1000,
            splitType="exact",
            participants=[
                {"memberId": ids[0], "raw": 500},
                {"memberId": ids[1], "raw": 300},
                {"memberId": ids[2], "raw": 200},
            ],
        ).json()
        assert share_map(body) == {ids[0]: 500, ids[1]: 300, ids[2]: 200}

    def test_exact_split_allows_a_zero_share(self, alice: Actor, trio: GroupCtx):
        ids = list(trio.members.values())
        body = create(
            alice,
            trio,
            amountMinor=1000,
            splitType="exact",
            participants=[
                {"memberId": ids[0], "raw": 1000},
                {"memberId": ids[1], "raw": 0},
                {"memberId": ids[2], "raw": 0},
            ],
        ).json()
        assert share_map(body)[ids[1]] == 0

    @pytest.mark.parametrize("amount", [1, 7, 99, 100, 1000, 8731, 999999])
    def test_every_equal_split_reconciles_exactly(
        self, alice: Actor, trio: GroupCtx, amount
    ):
        body = create(alice, trio, amountMinor=amount).json()
        assert sum(share_map(body).values()) == amount

    @pytest.mark.parametrize("amount", [1, 7, 99, 1000, 8731])
    def test_every_share_split_reconciles_exactly(
        self, alice: Actor, trio: GroupCtx, amount
    ):
        ids = list(trio.members.values())
        body = create(
            alice,
            trio,
            amountMinor=amount,
            splitType="share",
            participants=[
                {"memberId": ids[0], "raw": 1},
                {"memberId": ids[1], "raw": 2},
                {"memberId": ids[2], "raw": 3},
            ],
        ).json()
        assert sum(share_map(body).values()) == amount

    @pytest.mark.parametrize("amount", [1, 7, 99, 1000, 8731])
    def test_every_percentage_split_reconciles_exactly(
        self, alice: Actor, trio: GroupCtx, amount
    ):
        ids = list(trio.members.values())
        body = create(
            alice,
            trio,
            amountMinor=amount,
            splitType="percentage",
            participants=[
                {"memberId": ids[0], "raw": 33.33},
                {"memberId": ids[1], "raw": 33.33},
                {"memberId": ids[2], "raw": 33.34},
            ],
        ).json()
        assert sum(share_map(body).values()) == amount


class TestExpenseValidation:
    def bad(self, alice: Actor, trio: GroupCtx, **overrides):
        response = create(alice, trio, **overrides)
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "validation"
        return response.json()["message"]

    @pytest.mark.parametrize("description", ["", "   "])
    def test_description_is_required(self, alice, trio, description):
        self.bad(alice, trio, description=description)

    @pytest.mark.parametrize("amount", [0, -1, -9000])
    def test_amount_must_be_positive(self, alice, trio, amount):
        self.bad(alice, trio, amountMinor=amount)

    def test_amount_must_be_an_integer(self, alice: Actor, trio: GroupCtx):
        response = create(alice, trio, amountMinor=87.3)
        assert response.status_code == 400

    def test_payer_must_be_a_group_member(self, alice: Actor, trio: GroupCtx):
        self.bad(alice, trio, payerMemberId="mbr_ghost")

    def test_at_least_one_real_participant_is_required(self, alice, trio):
        message = self.bad(alice, trio, participants=[{"memberId": "mbr_ghost"}])
        assert "participant" in message.lower()

    def test_empty_participant_list_is_rejected(self, alice, trio):
        self.bad(alice, trio, participants=[])

    def test_percentages_must_total_100(self, alice: Actor, trio: GroupCtx):
        ids = list(trio.members.values())
        message = self.bad(
            alice,
            trio,
            splitType="percentage",
            participants=[
                {"memberId": ids[0], "raw": 50},
                {"memberId": ids[1], "raw": 20},
                {"memberId": ids[2], "raw": 20},
            ],
        )
        assert "100%" in message

    def test_shares_must_be_positive(self, alice: Actor, trio: GroupCtx):
        ids = list(trio.members.values())
        self.bad(
            alice,
            trio,
            splitType="share",
            participants=[{"memberId": ids[0], "raw": 0}, {"memberId": ids[1], "raw": 1}],
        )

    def test_share_split_requires_a_raw_value(self, alice: Actor, trio: GroupCtx):
        ids = list(trio.members.values())
        self.bad(alice, trio, splitType="share", participants=[{"memberId": ids[0]}])

    def test_exact_amounts_must_reconcile(self, alice: Actor, trio: GroupCtx):
        ids = list(trio.members.values())
        message = self.bad(
            alice,
            trio,
            amountMinor=1000,
            splitType="exact",
            participants=[
                {"memberId": ids[0], "raw": 400},
                {"memberId": ids[1], "raw": 400},
            ],
        )
        assert "reconcile" in message.lower()

    def test_exact_amounts_cannot_be_negative(self, alice: Actor, trio: GroupCtx):
        ids = list(trio.members.values())
        self.bad(
            alice,
            trio,
            amountMinor=1000,
            splitType="exact",
            participants=[
                {"memberId": ids[0], "raw": 1100},
                {"memberId": ids[1], "raw": -100},
            ],
        )

    def test_unknown_split_type_is_rejected(self, alice: Actor, trio: GroupCtx):
        self.bad(alice, trio, splitType="magic")

    def test_malformed_date_is_rejected(self, alice: Actor, trio: GroupCtx):
        self.bad(alice, trio, date="not-a-date")

    def test_missing_required_field_is_a_400(self, alice: Actor, trio: GroupCtx):
        body = payload(trio, alice)
        del body["payerMemberId"]
        response = alice.post(f"/groups/{trio.id}/expenses", json=body)
        assert response.status_code == 400
        assert response.json()["code"] == "validation"

    def test_a_rejected_create_writes_nothing(self, alice: Actor, trio: GroupCtx):
        before = alice.get(f"/groups/{trio.id}").json()
        response = create(
            alice,
            trio,
            splitType="percentage",
            participants=[
                {"memberId": m, "raw": 10} for m in trio.members.values()
            ],
        )
        assert response.status_code == 400

        after = alice.get(f"/groups/{trio.id}").json()
        assert after["expenses"] == before["expenses"] == []
        assert after["balances"] == before["balances"]


class TestCreateExpenseAccess:
    def test_non_member_is_403(self, bob: Actor, group: GroupCtx):
        me = group.members[list(group.members)[0]]
        response = bob.post(
            f"/groups/{group.id}/expenses",
            json={
                "description": "X",
                "date": "2026-09-02",
                "amountMinor": 100,
                "payerMemberId": me,
                "splitType": "equal",
                "participants": [{"memberId": me}],
            },
        )
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    def test_unknown_group_is_404(self, alice: Actor):
        response = alice.post(
            "/groups/grp_missing/expenses",
            json={
                "description": "X",
                "date": "2026-09-02",
                "amountMinor": 100,
                "payerMemberId": "mbr_x",
                "splitType": "equal",
                "participants": [{"memberId": "mbr_x"}],
            },
        )
        assert response.status_code == 404

    def test_requires_a_session(self, client, trio: GroupCtx):
        response = client.post(f"{API}/groups/{trio.id}/expenses", json={})
        assert response.status_code == 401


class TestUpdateExpense:
    def test_replaces_the_expense_and_bumps_the_version(
        self, alice: Actor, trio: GroupCtx
    ):
        created = create(alice, trio).json()
        response = alice.put(
            f"/groups/{trio.id}/expenses/{created['id']}",
            json=payload(trio, alice, description="Brunch", amountMinor=6000),
            headers=alice.if_match(created["version"]),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == created["id"]
        assert body["description"] == "Brunch"
        assert body["amountMinor"] == 6000
        assert body["version"] == 2
        assert response.headers["ETag"] == '"2"'
        assert sorted(share_map(body).values()) == [2000, 2000, 2000]
        assert body["updatedAt"] > body["createdAt"]

    def test_any_member_may_edit_any_expense(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        created = create(alice, trio).json()
        response = bob.put(
            f"/groups/{trio.id}/expenses/{created['id']}",
            json=payload(trio, alice, description="Bob edited"),
            headers=bob.if_match(created["version"]),
        )
        assert response.status_code == 200
        assert response.json()["description"] == "Bob edited"
        assert response.json()["createdByUserId"] == alice.user_id

    def test_balances_reflect_the_edit(self, alice: Actor, trio: GroupCtx):
        created = create(alice, trio).json()
        alice.put(
            f"/groups/{trio.id}/expenses/{created['id']}",
            json=payload(trio, alice, amountMinor=3000),
            headers=alice.if_match(created["version"]),
        )
        balances = {
            b["memberId"]: b["netMinor"]
            for b in alice.get(f"/groups/{trio.id}").json()["balances"]
        }
        assert balances[trio.members[alice.email]] == 2000

    def test_missing_if_match_is_412(self, alice: Actor, trio: GroupCtx):
        created = create(alice, trio).json()
        response = alice.put(
            f"/groups/{trio.id}/expenses/{created['id']}",
            json=payload(trio, alice),
        )
        assert response.status_code == 412
        assert response.json()["code"] == "precondition_required"

    def test_stale_if_match_is_409_with_the_current_record(
        self, alice: Actor, trio: GroupCtx
    ):
        created = create(alice, trio).json()
        alice.put(
            f"/groups/{trio.id}/expenses/{created['id']}",
            json=payload(trio, alice, description="First"),
            headers=alice.if_match(1),
        )
        response = alice.put(
            f"/groups/{trio.id}/expenses/{created['id']}",
            json=payload(trio, alice, description="Second"),
            headers=alice.if_match(1),
        )
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "version_conflict"
        assert body["current"]["description"] == "First"
        assert body["current"]["version"] == 2

    def test_validation_still_applies(self, alice: Actor, trio: GroupCtx):
        created = create(alice, trio).json()
        response = alice.put(
            f"/groups/{trio.id}/expenses/{created['id']}",
            json=payload(trio, alice, amountMinor=0),
            headers=alice.if_match(created["version"]),
        )
        assert response.status_code == 400

    def test_expense_from_another_group_is_404(
        self, alice: Actor, trio: GroupCtx
    ):
        from tests.conftest import make_group

        other = make_group(alice, name="Other")
        created = create(alice, trio).json()
        response = alice.put(
            f"/groups/{other.id}/expenses/{created['id']}",
            json={
                "description": "X",
                "date": "2026-09-02",
                "amountMinor": 100,
                "payerMemberId": other.members[alice.email],
                "splitType": "equal",
                "participants": [{"memberId": other.members[alice.email]}],
            },
            headers=alice.if_match(1),
        )
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    def test_unknown_expense_is_404(self, alice: Actor, trio: GroupCtx):
        response = alice.put(
            f"/groups/{trio.id}/expenses/exp_missing",
            json=payload(trio, alice),
            headers=alice.if_match(1),
        )
        assert response.status_code == 404

    def test_non_member_is_403(self, alice: Actor, bob: Actor, group: GroupCtx):
        me = group.members[alice.email]
        created = alice.post(
            f"/groups/{group.id}/expenses",
            json={
                "description": "X",
                "date": "2026-09-02",
                "amountMinor": 100,
                "payerMemberId": me,
                "splitType": "equal",
                "participants": [{"memberId": me}],
            },
        ).json()
        response = bob.put(
            f"/groups/{group.id}/expenses/{created['id']}",
            json={
                "description": "Y",
                "date": "2026-09-02",
                "amountMinor": 100,
                "payerMemberId": me,
                "splitType": "equal",
                "participants": [{"memberId": me}],
            },
            headers=bob.if_match(1),
        )
        assert response.status_code == 403


class TestDeleteExpense:
    def test_removes_it_and_updates_balances(self, alice: Actor, trio: GroupCtx):
        created = create(alice, trio).json()
        response = alice.delete(
            f"/groups/{trio.id}/expenses/{created['id']}",
            headers=alice.if_match(created["version"]),
        )
        assert response.status_code == 204

        snapshot = alice.get(f"/groups/{trio.id}").json()
        assert snapshot["expenses"] == []
        assert all(b["netMinor"] == 0 for b in snapshot["balances"])

    def test_any_member_may_delete_any_expense(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        created = create(alice, trio).json()
        response = bob.delete(
            f"/groups/{trio.id}/expenses/{created['id']}",
            headers=bob.if_match(created["version"]),
        )
        assert response.status_code == 204

    def test_missing_if_match_is_412(self, alice: Actor, trio: GroupCtx):
        created = create(alice, trio).json()
        response = alice.delete(f"/groups/{trio.id}/expenses/{created['id']}")
        assert response.status_code == 412

    def test_stale_if_match_is_409(self, alice: Actor, trio: GroupCtx):
        created = create(alice, trio).json()
        alice.put(
            f"/groups/{trio.id}/expenses/{created['id']}",
            json=payload(trio, alice, description="Edited"),
            headers=alice.if_match(1),
        )
        response = alice.delete(
            f"/groups/{trio.id}/expenses/{created['id']}", headers=alice.if_match(1)
        )
        assert response.status_code == 409
        assert response.json()["code"] == "version_conflict"

    def test_unknown_expense_is_404(self, alice: Actor, trio: GroupCtx):
        response = alice.delete(
            f"/groups/{trio.id}/expenses/exp_missing", headers=alice.if_match(1)
        )
        assert response.status_code == 404

    def test_deleting_twice_is_404(self, alice: Actor, trio: GroupCtx):
        created = create(alice, trio).json()
        alice.delete(
            f"/groups/{trio.id}/expenses/{created['id']}", headers=alice.if_match(1)
        )
        response = alice.delete(
            f"/groups/{trio.id}/expenses/{created['id']}", headers=alice.if_match(1)
        )
        assert response.status_code == 404

    def test_non_member_is_403(self, alice: Actor, bob: Actor, group: GroupCtx):
        me = group.members[alice.email]
        created = alice.post(
            f"/groups/{group.id}/expenses",
            json={
                "description": "X",
                "date": "2026-09-02",
                "amountMinor": 100,
                "payerMemberId": me,
                "splitType": "equal",
                "participants": [{"memberId": me}],
            },
        ).json()
        response = bob.delete(
            f"/groups/{group.id}/expenses/{created['id']}", headers=bob.if_match(1)
        )
        assert response.status_code == 403
