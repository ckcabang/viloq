"""GET+POST /groups, GET+PATCH /groups/{id}, PATCH /groups/{id}/me."""

from __future__ import annotations

import pytest

from tests.conftest import API, Actor, GroupCtx, join, make_group


def add_expense(actor: Actor, group: GroupCtx, **overrides) -> dict:
    """Post a minimal valid expense, paid by and split over the caller alone."""
    me = group.members[actor.email]
    payload = {
        "description": "Taxi",
        "date": "2026-09-02",
        "amountMinor": 1000,
        "payerMemberId": me,
        "splitType": "equal",
        "participants": [{"memberId": me}],
    }
    payload.update(overrides)
    return actor.post(f"/groups/{group.id}/expenses", json=payload).json()


class TestCreateGroup:
    def test_returns_the_group_and_an_etag(self, alice: Actor):
        response = alice.post(
            "/groups",
            json={"name": "Lisbon Trip", "currency": "EUR", "displayName": "Alice"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Lisbon Trip"
        assert body["currency"] == "EUR"
        assert body["createdByUserId"] == alice.user_id
        assert body["version"] == 1
        assert body["inviteRevoked"] is False
        assert response.headers["ETag"] == '"1"'

    def test_invite_code_is_formatted_and_unambiguous(self, alice: Actor):
        code = alice.post(
            "/groups", json={"name": "T", "currency": "USD", "displayName": "A"}
        ).json()["inviteCode"]
        assert len(code) == 11
        assert code[3] == "-"
        assert code[7] == "-"
        assert set(code) <= set("ABCDEFGHJKMNPQRSTUVWXYZ23456789-")

    def test_invite_codes_differ_between_groups(self, alice: Actor):
        codes = {
            alice.post(
                "/groups",
                json={"name": f"G{i}", "currency": "USD", "displayName": "A"},
            ).json()["inviteCode"]
            for i in range(5)
        }
        assert len(codes) == 5

    def test_creates_the_callers_membership_in_one_step(self, alice: Actor):
        group = alice.post(
            "/groups", json={"name": "T", "currency": "USD", "displayName": "Ally"}
        ).json()
        snapshot = alice.get(f"/groups/{group['id']}").json()
        assert snapshot["me"]["displayName"] == "Ally"
        assert snapshot["me"]["userId"] == alice.user_id
        assert len(snapshot["members"]) == 1

    def test_trims_names(self, alice: Actor):
        group = alice.post(
            "/groups",
            json={"name": "  Trip  ", "currency": "USD", "displayName": "  Ally  "},
        ).json()
        assert group["name"] == "Trip"
        snapshot = alice.get(f"/groups/{group['id']}").json()
        assert snapshot["me"]["displayName"] == "Ally"

    @pytest.mark.parametrize(
        "payload",
        [
            {"name": "", "currency": "EUR", "displayName": "A"},
            {"name": "   ", "currency": "EUR", "displayName": "A"},
            {"name": "T", "currency": "EUR", "displayName": ""},
            {"name": "T", "currency": "EUR"},
            {"name": "T", "displayName": "A"},
            {"name": "T", "currency": "XBT", "displayName": "A"},
        ],
    )
    def test_rejects_invalid_input(self, alice: Actor, payload):
        response = alice.post("/groups", json=payload)
        assert response.status_code == 400
        assert response.json()["code"] == "validation"

    def test_requires_a_session(self, client):
        response = client.post(
            f"{API}/groups",
            json={"name": "T", "currency": "EUR", "displayName": "A"},
        )
        assert response.status_code == 401


class TestListGroups:
    def test_is_empty_for_a_new_user(self, alice: Actor):
        assert alice.get("/groups").json() == []

    def test_lists_only_the_callers_groups(self, alice: Actor, bob: Actor):
        mine = make_group(alice, name="Mine")
        make_group(bob, name="Theirs", display_name="Bob")
        assert [g["id"] for g in alice.get("/groups").json()] == [mine.id]

    def test_sorts_by_name_case_insensitively(self, alice: Actor):
        for name in ["banana", "Apple", "cherry"]:
            make_group(alice, name=name)
        assert [g["name"] for g in alice.get("/groups").json()] == [
            "Apple",
            "banana",
            "cherry",
        ]

    def test_summary_counts_and_net_balance(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        add_expense(
            alice,
            trio,
            description="Dinner",
            amountMinor=9000,
            participants=[{"memberId": m} for m in trio.members.values()],
        )
        row = next(g for g in alice.get("/groups").json() if g["id"] == trio.id)
        assert row["memberCount"] == 3
        assert row["expenseCount"] == 1
        assert row["myMemberId"] == trio.members[alice.email]
        assert row["myDisplayName"] == "Alice"
        assert row["myNetMinor"] == 6000  # paid 9000, owes a 3000 share

        bob_row = next(g for g in bob.get("/groups").json() if g["id"] == trio.id)
        assert bob_row["myNetMinor"] == -3000

    def test_requires_a_session(self, client):
        assert client.get(f"{API}/groups").status_code == 401


class TestGroupSnapshot:
    def test_returns_every_section(self, alice: Actor, trio: GroupCtx):
        body = alice.get(f"/groups/{trio.id}").json()
        assert set(body) == {
            "group",
            "me",
            "members",
            "expenses",
            "payments",
            "balances",
        }
        assert body["group"]["id"] == trio.id
        assert body["group"]["isCreator"] is True
        assert body["group"]["currencyLocked"] is False
        assert body["group"]["invitePath"] == f"#/join?code={trio.invite_code}"
        assert len(body["balances"]) == 3

    def test_members_are_sorted_by_display_name_and_flag_the_caller(
        self, alice: Actor, bob: Actor, carol: Actor
    ):
        ctx = make_group(alice, display_name="zoe")
        join(ctx, bob, "Bob")
        join(ctx, carol, "amy")

        members = alice.get(f"/groups/{ctx.id}").json()["members"]
        assert [m["displayName"] for m in members] == ["amy", "Bob", "zoe"]
        assert [m["isMe"] for m in members] == [False, False, True]
        assert all(m["groupId"] == ctx.id for m in members)

    def test_non_creator_sees_is_creator_false(self, bob: Actor, trio: GroupCtx):
        assert bob.get(f"/groups/{trio.id}").json()["group"]["isCreator"] is False

    def test_currency_locks_once_an_expense_exists(self, alice: Actor, trio: GroupCtx):
        add_expense(alice, trio)
        snapshot = alice.get(f"/groups/{trio.id}").json()
        assert snapshot["group"]["currencyLocked"] is True

    def test_expenses_are_newest_first(self, alice: Actor, trio: GroupCtx):
        for day, label in [
            ("2026-09-01", "old"),
            ("2026-09-05", "new"),
            ("2026-09-03", "mid"),
        ]:
            add_expense(alice, trio, date=day, description=label)
        descriptions = [
            e["description"] for e in alice.get(f"/groups/{trio.id}").json()["expenses"]
        ]
        assert descriptions == ["new", "mid", "old"]

    def test_payments_carry_can_manage_per_viewer(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        alice.post(
            f"/groups/{trio.id}/payments",
            json={
                "recipientMemberId": trio.members[bob.email],
                "amountMinor": 500,
                "date": "2026-09-02",
            },
        )
        alice_view = alice.get(f"/groups/{trio.id}").json()["payments"][0]
        bob_view = bob.get(f"/groups/{trio.id}").json()["payments"][0]
        assert alice_view["canManage"] is True
        assert bob_view["canManage"] is False

    def test_unknown_group_is_404(self, alice: Actor):
        response = alice.get("/groups/grp_missing")
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    def test_non_member_is_403(self, bob: Actor, group: GroupCtx):
        response = bob.get(f"/groups/{group.id}")
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    def test_requires_a_session(self, client, group: GroupCtx):
        assert client.get(f"{API}/groups/{group.id}").status_code == 401


class TestUpdateGroup:
    def test_renames_and_bumps_the_version(self, alice: Actor, group: GroupCtx):
        response = alice.patch(
            f"/groups/{group.id}",
            json={"name": "Lisbon 2026"},
            headers=alice.if_match(group.version),
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Lisbon 2026"
        assert response.json()["version"] == group.version + 1
        assert response.headers["ETag"] == f'"{group.version + 1}"'

    def test_changes_currency_while_the_group_has_no_expenses(
        self, alice: Actor, group: GroupCtx
    ):
        response = alice.patch(
            f"/groups/{group.id}",
            json={"currency": "JPY"},
            headers=alice.if_match(group.version),
        )
        assert response.status_code == 200
        assert response.json()["currency"] == "JPY"

    def test_currency_is_locked_once_an_expense_exists(
        self, alice: Actor, group: GroupCtx
    ):
        add_expense(alice, group)
        response = alice.patch(
            f"/groups/{group.id}",
            json={"currency": "USD"},
            headers=alice.if_match(group.version),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "currency_locked"

    def test_resending_the_same_currency_is_not_locked(
        self, alice: Actor, group: GroupCtx
    ):
        add_expense(alice, group)
        response = alice.patch(
            f"/groups/{group.id}",
            json={"name": "Same currency", "currency": "EUR"},
            headers=alice.if_match(group.version),
        )
        assert response.status_code == 200

    def test_only_the_creator_may_update(self, bob: Actor, trio: GroupCtx):
        response = bob.patch(
            f"/groups/{trio.id}",
            json={"name": "Hijacked"},
            headers=bob.if_match(trio.version),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    def test_non_member_is_403(self, bob: Actor, group: GroupCtx):
        response = bob.patch(
            f"/groups/{group.id}",
            json={"name": "Nope"},
            headers=bob.if_match(group.version),
        )
        assert response.status_code == 403

    def test_missing_if_match_is_412(self, alice: Actor, group: GroupCtx):
        response = alice.patch(f"/groups/{group.id}", json={"name": "X"})
        assert response.status_code == 412
        assert response.json()["code"] == "precondition_required"

    def test_stale_if_match_is_409_with_the_current_record(
        self, alice: Actor, group: GroupCtx
    ):
        alice.patch(
            f"/groups/{group.id}",
            json={"name": "First"},
            headers=alice.if_match(group.version),
        )
        response = alice.patch(
            f"/groups/{group.id}",
            json={"name": "Second"},
            headers=alice.if_match(group.version),
        )
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "version_conflict"
        assert body["current"]["name"] == "First"
        assert body["current"]["version"] == group.version + 1

    def test_accepts_an_unquoted_if_match(self, alice: Actor, group: GroupCtx):
        response = alice.patch(
            f"/groups/{group.id}",
            json={"name": "Bare"},
            headers={"If-Match": str(group.version)},
        )
        assert response.status_code == 200

    def test_unparseable_if_match_is_412(self, alice: Actor, group: GroupCtx):
        response = alice.patch(
            f"/groups/{group.id}",
            json={"name": "X"},
            headers={"If-Match": '"not-a-number"'},
        )
        assert response.status_code == 412

    def test_empty_patch_is_rejected(self, alice: Actor, group: GroupCtx):
        response = alice.patch(
            f"/groups/{group.id}", json={}, headers=alice.if_match(group.version)
        )
        assert response.status_code == 400
        assert response.json()["code"] == "validation"

    def test_blank_name_is_rejected(self, alice: Actor, group: GroupCtx):
        response = alice.patch(
            f"/groups/{group.id}",
            json={"name": "   "},
            headers=alice.if_match(group.version),
        )
        assert response.status_code == 400

    def test_unknown_group_is_404(self, alice: Actor):
        response = alice.patch(
            "/groups/grp_missing", json={"name": "X"}, headers=alice.if_match(1)
        )
        assert response.status_code == 404


class TestUpdateMyMemberName:
    def test_renames_only_the_caller_in_this_group(
        self, alice: Actor, bob: Actor, trio: GroupCtx
    ):
        response = bob.patch(f"/groups/{trio.id}/me", json={"displayName": "  Bobby  "})
        assert response.status_code == 200
        body = response.json()
        assert body["displayName"] == "Bobby"
        assert body["id"] == trio.members[bob.email]
        assert body["groupId"] == trio.id

        names = {
            m["displayName"] for m in alice.get(f"/groups/{trio.id}").json()["members"]
        }
        assert names == {"Alice", "Bobby", "Carol"}

    def test_does_not_change_the_account_display_name(self, bob: Actor, trio: GroupCtx):
        before = bob.get("/auth/me").json()["displayName"]
        bob.patch(f"/groups/{trio.id}/me", json={"displayName": "Bobby"})
        assert bob.get("/auth/me").json()["displayName"] == before

    @pytest.mark.parametrize("name", ["", "  "])
    def test_rejects_an_empty_name(self, alice: Actor, group: GroupCtx, name):
        response = alice.patch(f"/groups/{group.id}/me", json={"displayName": name})
        assert response.status_code == 400

    def test_non_member_is_403(self, bob: Actor, group: GroupCtx):
        response = bob.patch(f"/groups/{group.id}/me", json={"displayName": "B"})
        assert response.status_code == 403

    def test_unknown_group_is_404(self, alice: Actor):
        response = alice.patch("/groups/grp_missing/me", json={"displayName": "A"})
        assert response.status_code == 404
