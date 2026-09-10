"""POST /groups/{id}/payments, PUT+DELETE /groups/{id}/payments/{paymentId}."""

from __future__ import annotations

import pytest

from tests.conftest import API, Actor, GroupCtx, as_datetime


def pay(actor: Actor, group: GroupCtx, recipient: Actor, **overrides):
    body = {
        "recipientMemberId": group.members[recipient.email],
        "amountMinor": 2500,
        "date": "2026-09-04",
    }
    body.update(overrides)
    return actor.post(f"/groups/{group.id}/payments", json=body)


class TestCreatePayment:
    def test_records_the_caller_as_payer(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        response = pay(alice, trio, bob)
        assert response.status_code == 201
        body = response.json()
        assert body["payerMemberId"] == trio.members[alice.email]
        assert body["recipientMemberId"] == trio.members[bob.email]
        assert body["amountMinor"] == 2500
        assert body["date"] == "2026-09-04"
        assert body["note"] == ""
        assert body["createdByUserId"] == alice.user_id
        assert body["version"] == 1
        assert body["canManage"] is True
        assert response.headers["ETag"] == '"1"'

    def test_ignores_any_payer_supplied_in_the_body(
        self, alice: Actor, bob: Actor, carol: Actor, trio: GroupCtx
    ):
        response = pay(alice, trio, bob, payerMemberId=trio.members[carol.email])
        assert response.status_code == 201
        assert response.json()["payerMemberId"] == trio.members[alice.email]

    def test_trims_the_note(self, alice: Actor, bob: Actor, trio: GroupCtx):
        body = pay(alice, trio, bob, note="  cash  ").json()
        assert body["note"] == "cash"

    def test_moves_both_balances_toward_zero(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        alice_id = trio.members[alice.email]
        bob_id = trio.members[bob.email]
        alice.post(
            f"/groups/{trio.id}/expenses",
            json={
                "description": "Dinner",
                "date": "2026-09-02",
                "amountMinor": 6000,
                "payerMemberId": alice_id,
                "splitType": "equal",
                "participants": [{"memberId": alice_id}, {"memberId": bob_id}],
            },
        )
        bob.post(
            f"/groups/{trio.id}/payments",
            json={
                "recipientMemberId": alice_id,
                "amountMinor": 3000,
                "date": "2026-09-04",
            },
        )
        balances = {
            b["memberId"]: b["netMinor"]
            for b in alice.get(f"/groups/{trio.id}").json()["balances"]
        }
        assert balances[alice_id] == 0
        assert balances[bob_id] == 0

    def test_recipient_must_be_a_member(self, alice: Actor, trio: GroupCtx):
        response = alice.post(
            f"/groups/{trio.id}/payments",
            json={
                "recipientMemberId": "mbr_ghost",
                "amountMinor": 100,
                "date": "2026-09-04",
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == "validation"

    def test_recipient_from_another_group_is_rejected(
        self, alice: Actor, trio: GroupCtx
    ):
        from tests.conftest import make_group

        other = make_group(alice, name="Other")
        response = alice.post(
            f"/groups/{trio.id}/payments",
            json={
                "recipientMemberId": other.members[alice.email],
                "amountMinor": 100,
                "date": "2026-09-04",
            },
        )
        assert response.status_code == 400

    def test_payer_and_recipient_must_differ(self, alice: Actor, trio: GroupCtx):
        response = pay(alice, trio, alice)
        assert response.status_code == 400
        assert "differ" in response.json()["message"]

    @pytest.mark.parametrize("amount", [0, -1])
    def test_amount_must_be_positive(self, alice, bob, trio, amount):
        response = pay(alice, trio, bob, amountMinor=amount)
        assert response.status_code == 400

    def test_date_is_required(self, alice: Actor, bob: Actor, trio: GroupCtx):
        response = alice.post(
            f"/groups/{trio.id}/payments",
            json={
                "recipientMemberId": trio.members[bob.email],
                "amountMinor": 100,
            },
        )
        assert response.status_code == 400

    def test_non_member_is_403(self, alice: Actor, bob: Actor, group: GroupCtx):
        response = bob.post(
            f"/groups/{group.id}/payments",
            json={
                "recipientMemberId": group.members[alice.email],
                "amountMinor": 100,
                "date": "2026-09-04",
            },
        )
        assert response.status_code == 403

    def test_unknown_group_is_404(self, alice: Actor):
        response = alice.post(
            "/groups/grp_missing/payments",
            json={
                "recipientMemberId": "mbr_x",
                "amountMinor": 100,
                "date": "2026-09-04",
            },
        )
        assert response.status_code == 404

    def test_requires_a_session(self, client, trio: GroupCtx):
        assert client.post(f"{API}/groups/{trio.id}/payments", json={}).status_code == 401


class TestUpdatePayment:
    def test_replaces_the_payment_and_bumps_the_version(
        self, alice: Actor, bob: Actor, carol: Actor, trio: GroupCtx
    ):
        created = pay(alice, trio, bob).json()
        response = alice.put(
            f"/groups/{trio.id}/payments/{created['id']}",
            json={
                "recipientMemberId": trio.members[carol.email],
                "amountMinor": 900,
                "date": "2026-09-06",
                "note": "corrected",
            },
            headers=alice.if_match(created["version"]),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["recipientMemberId"] == trio.members[carol.email]
        assert body["amountMinor"] == 900
        assert body["date"] == "2026-09-06"
        assert body["note"] == "corrected"
        assert body["version"] == 2
        assert response.headers["ETag"] == '"2"'
        assert as_datetime(body["updatedAt"]) > as_datetime(body["createdAt"])

    def test_the_payer_never_changes(self, alice: Actor, bob: Actor, trio: GroupCtx):
        created = pay(alice, trio, bob).json()
        response = alice.put(
            f"/groups/{trio.id}/payments/{created['id']}",
            json={
                "recipientMemberId": trio.members[bob.email],
                "amountMinor": 900,
                "date": "2026-09-06",
                "payerMemberId": trio.members[bob.email],
            },
            headers=alice.if_match(created["version"]),
        )
        assert response.status_code == 200
        assert response.json()["payerMemberId"] == trio.members[alice.email]

    def test_only_the_recorder_may_edit(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        created = pay(alice, trio, bob).json()
        response = bob.put(
            f"/groups/{trio.id}/payments/{created['id']}",
            json={
                "recipientMemberId": trio.members[bob.email],
                "amountMinor": 100,
                "date": "2026-09-06",
            },
            headers=bob.if_match(created["version"]),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    def test_recipient_may_not_become_the_payer(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        created = pay(alice, trio, bob).json()
        response = alice.put(
            f"/groups/{trio.id}/payments/{created['id']}",
            json={
                "recipientMemberId": trio.members[alice.email],
                "amountMinor": 100,
                "date": "2026-09-06",
            },
            headers=alice.if_match(created["version"]),
        )
        assert response.status_code == 400
        assert "differ" in response.json()["message"]

    def test_missing_if_match_is_412(self, alice: Actor, bob: Actor, trio: GroupCtx):
        created = pay(alice, trio, bob).json()
        response = alice.put(
            f"/groups/{trio.id}/payments/{created['id']}",
            json={
                "recipientMemberId": trio.members[bob.email],
                "amountMinor": 100,
                "date": "2026-09-06",
            },
        )
        assert response.status_code == 412

    def test_stale_if_match_is_409_with_the_current_record(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        created = pay(alice, trio, bob).json()
        body = {
            "recipientMemberId": trio.members[bob.email],
            "amountMinor": 111,
            "date": "2026-09-06",
        }
        alice.put(
            f"/groups/{trio.id}/payments/{created['id']}",
            json=body,
            headers=alice.if_match(1),
        )
        response = alice.put(
            f"/groups/{trio.id}/payments/{created['id']}",
            json=body,
            headers=alice.if_match(1),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "version_conflict"
        assert response.json()["current"]["amountMinor"] == 111

    def test_unknown_payment_is_404(self, alice: Actor, bob: Actor, trio: GroupCtx):
        response = alice.put(
            f"/groups/{trio.id}/payments/pay_missing",
            json={
                "recipientMemberId": trio.members[bob.email],
                "amountMinor": 100,
                "date": "2026-09-06",
            },
            headers=alice.if_match(1),
        )
        assert response.status_code == 404

    def test_non_member_is_403(self, alice: Actor, bob: Actor, carol: Actor, trio):
        created = pay(alice, trio, bob).json()
        from tests.conftest import sign_in

        outsider = sign_in(alice.client, "dana@example.com")
        response = outsider.put(
            f"/groups/{trio.id}/payments/{created['id']}",
            json={
                "recipientMemberId": trio.members[bob.email],
                "amountMinor": 100,
                "date": "2026-09-06",
            },
            headers=outsider.if_match(1),
        )
        assert response.status_code == 403


class TestDeletePayment:
    def test_removes_it_and_updates_balances(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        created = pay(alice, trio, bob).json()
        response = alice.delete(
            f"/groups/{trio.id}/payments/{created['id']}",
            headers=alice.if_match(created["version"]),
        )
        assert response.status_code == 204

        snapshot = alice.get(f"/groups/{trio.id}").json()
        assert snapshot["payments"] == []
        assert all(b["netMinor"] == 0 for b in snapshot["balances"])

    def test_only_the_recorder_may_delete(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        created = pay(alice, trio, bob).json()
        response = bob.delete(
            f"/groups/{trio.id}/payments/{created['id']}",
            headers=bob.if_match(created["version"]),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    def test_missing_if_match_is_412(self, alice: Actor, bob: Actor, trio: GroupCtx):
        created = pay(alice, trio, bob).json()
        response = alice.delete(f"/groups/{trio.id}/payments/{created['id']}")
        assert response.status_code == 412

    def test_stale_if_match_is_409(self, alice: Actor, bob: Actor, trio: GroupCtx):
        created = pay(alice, trio, bob).json()
        alice.put(
            f"/groups/{trio.id}/payments/{created['id']}",
            json={
                "recipientMemberId": trio.members[bob.email],
                "amountMinor": 111,
                "date": "2026-09-06",
            },
            headers=alice.if_match(1),
        )
        response = alice.delete(
            f"/groups/{trio.id}/payments/{created['id']}", headers=alice.if_match(1)
        )
        assert response.status_code == 409

    def test_unknown_payment_is_404(self, alice: Actor, trio: GroupCtx):
        response = alice.delete(
            f"/groups/{trio.id}/payments/pay_missing", headers=alice.if_match(1)
        )
        assert response.status_code == 404

    def test_deleting_twice_is_404(self, alice: Actor, bob: Actor, trio: GroupCtx):
        created = pay(alice, trio, bob).json()
        alice.delete(
            f"/groups/{trio.id}/payments/{created['id']}", headers=alice.if_match(1)
        )
        response = alice.delete(
            f"/groups/{trio.id}/payments/{created['id']}", headers=alice.if_match(1)
        )
        assert response.status_code == 404
