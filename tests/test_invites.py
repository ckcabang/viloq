"""GET /invites/{code}, POST /invites/{code}/join, POST+DELETE /groups/{id}/invite."""

from __future__ import annotations

import pytest

from tests.conftest import API, Actor, GroupCtx


class TestInviteInfo:
    def test_is_readable_without_a_session(self, client, group: GroupCtx):
        response = client.get(f"{API}/invites/{group.invite_code}")
        assert response.status_code == 200
        body = response.json()
        assert body["groupId"] == group.id
        assert body["groupName"] == "Lisbon Trip"
        assert body["currency"] == "EUR"
        assert body["revoked"] is False
        assert body["memberCount"] == 1

    def test_does_not_leak_member_emails_or_transactions(self, client, trio: GroupCtx):
        body = client.get(f"{API}/invites/{trio.invite_code}").json()
        assert set(body) == {
            "groupId",
            "groupName",
            "currency",
            "revoked",
            "memberCount",
        }
        assert body["memberCount"] == 3

    def test_member_count_tracks_joins(self, client, bob: Actor, group: GroupCtx):
        assert (
            client.get(f"{API}/invites/{group.invite_code}").json()["memberCount"] == 1
        )
        bob.post(f"/invites/{group.invite_code}/join", json={"displayName": "Bob"})
        assert (
            client.get(f"{API}/invites/{group.invite_code}").json()["memberCount"] == 2
        )

    def test_reports_revocation_rather_than_erroring(
        self, client, alice: Actor, group: GroupCtx
    ):
        alice.delete(f"/groups/{group.id}/invite")
        response = client.get(f"{API}/invites/{group.invite_code}")
        assert response.status_code == 200
        assert response.json()["revoked"] is True

    def test_unknown_code_is_404(self, client):
        response = client.get(f"{API}/invites/ZZZ-ZZZ-ZZZ")
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"


class TestJoinGroup:
    def test_adds_the_caller_as_a_member(self, bob: Actor, group: GroupCtx):
        response = bob.post(
            f"/invites/{group.invite_code}/join", json={"displayName": "  Bobby  "}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["group"]["id"] == group.id
        assert body["member"]["displayName"] == "Bobby"
        assert body["member"]["userId"] == bob.user_id
        assert body["member"]["groupId"] == group.id
        assert bob.get(f"/groups/{group.id}").status_code == 200

    def test_is_idempotent_and_keeps_the_original_name(
        self, bob: Actor, group: GroupCtx
    ):
        first = bob.post(
            f"/invites/{group.invite_code}/join", json={"displayName": "Bobby"}
        ).json()
        second = bob.post(
            f"/invites/{group.invite_code}/join", json={"displayName": "Renamed"}
        )
        assert second.status_code == 200
        assert second.json()["member"]["id"] == first["member"]["id"]
        assert second.json()["member"]["displayName"] == "Bobby"

        members = bob.get(f"/groups/{group.id}").json()["members"]
        assert len(members) == 2

    def test_a_user_can_belong_to_several_groups(self, alice: Actor, bob: Actor):
        from tests.conftest import make_group

        first = make_group(alice, name="One")
        second = make_group(alice, name="Two")
        for ctx in (first, second):
            bob.post(f"/invites/{ctx.invite_code}/join", json={"displayName": "Bob"})
        assert len(bob.get("/groups").json()) == 2

    def test_revoked_invite_is_403(self, alice: Actor, bob: Actor, group: GroupCtx):
        alice.delete(f"/groups/{group.id}/invite")
        response = bob.post(
            f"/invites/{group.invite_code}/join", json={"displayName": "Bob"}
        )
        assert response.status_code == 403
        assert response.json()["code"] == "invite_revoked"

    def test_unknown_code_is_404(self, bob: Actor):
        response = bob.post("/invites/ZZZ-ZZZ-ZZZ/join", json={"displayName": "Bob"})
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    @pytest.mark.parametrize("payload", [{}, {"displayName": ""}, {"displayName": "  "}])
    def test_requires_a_display_name(self, bob: Actor, group: GroupCtx, payload):
        response = bob.post(f"/invites/{group.invite_code}/join", json=payload)
        assert response.status_code == 400
        assert response.json()["code"] == "validation"

    def test_requires_a_session(self, client, group: GroupCtx):
        response = client.post(
            f"{API}/invites/{group.invite_code}/join", json={"displayName": "Bob"}
        )
        assert response.status_code == 401


class TestRegenerateInvite:
    def test_issues_a_new_code_and_retires_the_old_one(
        self, client, alice: Actor, bob: Actor, group: GroupCtx
    ):
        response = alice.post(f"/groups/{group.id}/invite")
        assert response.status_code == 200
        body = response.json()
        new_code = body["inviteCode"]
        assert new_code != group.invite_code
        assert body["invitePath"] == f"#/join?code={new_code}"

        assert client.get(f"{API}/invites/{group.invite_code}").status_code == 404
        assert client.get(f"{API}/invites/{new_code}").status_code == 200
        assert (
            bob.post(f"/invites/{new_code}/join", json={"displayName": "Bob"}).status_code
            == 200
        )

    def test_clears_the_revoked_flag(self, client, alice: Actor, group: GroupCtx):
        alice.delete(f"/groups/{group.id}/invite")
        new_code = alice.post(f"/groups/{group.id}/invite").json()["inviteCode"]
        assert client.get(f"{API}/invites/{new_code}").json()["revoked"] is False

    def test_is_reflected_in_the_snapshot(self, alice: Actor, group: GroupCtx):
        new_code = alice.post(f"/groups/{group.id}/invite").json()["inviteCode"]
        snapshot = alice.get(f"/groups/{group.id}").json()
        assert snapshot["group"]["inviteCode"] == new_code
        assert snapshot["group"]["invitePath"] == f"#/join?code={new_code}"

    def test_only_the_creator_may_rotate(self, bob: Actor, trio: GroupCtx):
        response = bob.post(f"/groups/{trio.id}/invite")
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    def test_non_member_is_403(self, bob: Actor, group: GroupCtx):
        assert bob.post(f"/groups/{group.id}/invite").status_code == 403

    def test_unknown_group_is_404(self, alice: Actor):
        assert alice.post("/groups/grp_missing/invite").status_code == 404

    def test_requires_a_session(self, client, group: GroupCtx):
        assert client.post(f"{API}/groups/{group.id}/invite").status_code == 401


class TestRevokeInvite:
    def test_blocks_further_joins(self, alice: Actor, bob: Actor, group: GroupCtx):
        assert alice.delete(f"/groups/{group.id}/invite").status_code == 204
        response = bob.post(
            f"/invites/{group.invite_code}/join", json={"displayName": "Bob"}
        )
        assert response.status_code == 403

    def test_does_not_remove_existing_members(self, alice: Actor, bob: Actor, trio):
        alice.delete(f"/groups/{trio.id}/invite")
        assert bob.get(f"/groups/{trio.id}").status_code == 200

    def test_only_the_creator_may_revoke(self, bob: Actor, trio: GroupCtx):
        response = bob.delete(f"/groups/{trio.id}/invite")
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    def test_unknown_group_is_404(self, alice: Actor):
        assert alice.delete("/groups/grp_missing/invite").status_code == 404

    def test_requires_a_session(self, client, group: GroupCtx):
        assert client.delete(f"{API}/groups/{group.id}/invite").status_code == 401
