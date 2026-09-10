"""The persistence layer in isolation: `app/db.py` and `app/models.py`.

The HTTP tests exercise storage indirectly through every endpoint. These pin the
behaviours the routers lean on but never state outright — atomic
read-modify-write, timezone normalisation, the JSON-backed split columns, and
the SQLite pragmas — so a change there fails here rather than somewhere subtle
three layers up.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import (
    Database,
    high_entropy_token,
    in_memory_database,
    invite_code,
    random_id,
)
from app.errors import ApiError
from app.models import (
    Expense,
    ExpenseShare,
    ExpenseShareList,
    Group,
    SplitInput,
    SplitInputList,
    User,
)


def make_user(db: Database, suffix: str = "1") -> User:
    return db.create_user(f"user{suffix}@example.com", display_name=f"User {suffix}")


def stamp() -> datetime:
    return datetime.now(UTC)


class TestTransaction:
    def test_commits_the_body_on_success(self, db: Database):
        with db.transaction():
            db.session.add(
                User(id="usr_ok", email="ok@example.com", created_at=stamp(), updated_at=stamp())
            )
        assert db.session.get(User, "usr_ok") is not None

    def test_rolls_back_when_the_body_raises(self, db: Database):
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.session.add(
                    User(id="usr_no", email="no@example.com", created_at=stamp(), updated_at=stamp())
                )
                raise RuntimeError("boom")
        assert db.session.get(User, "usr_no") is None

    def test_rolls_back_on_an_api_error_raised_mid_sequence(self, db: Database):
        """A rejected request must leave nothing behind."""
        with pytest.raises(ApiError):
            with db.transaction():
                db.session.add(
                    User(id="usr_half", email="half@example.com", created_at=stamp(), updated_at=stamp())
                )
                raise ApiError(400, "validation", "nope")
        assert db.session.get(User, "usr_half") is None

    def test_only_the_outermost_block_commits(self, db: Database):
        with db.transaction():
            with db.transaction():
                db.session.add(
                    User(id="usr_nested", email="nested@example.com", created_at=stamp(), updated_at=stamp())
                )
            # Inner block has exited but not committed: still one open transaction.
            assert db.session.in_transaction()
        assert not db.session.in_transaction()
        assert db.session.get(User, "usr_nested") is not None

    def test_an_inner_failure_rolls_back_the_whole_unit(self, db: Database):
        with pytest.raises(ApiError):
            with db.transaction():
                db.session.add(
                    User(id="usr_a", email="a@example.com", created_at=stamp(), updated_at=stamp())
                )
                with db.transaction():
                    db.session.add(
                        User(id="usr_b", email="b@example.com", created_at=stamp(), updated_at=stamp())
                    )
                    raise ApiError(409, "version_conflict", "clash")
        assert db.session.get(User, "usr_a") is None
        assert db.session.get(User, "usr_b") is None

    def test_a_lone_write_commits_without_a_block(self, db: Database):
        user = make_user(db)
        assert db.user_by_email(user.email) is not None


class TestUTCDateTime:
    """SQLite has no timezone type; the column normalises both ways."""

    def test_a_naive_value_comes_back_as_aware_utc(self, db: Database):
        naive = datetime(2026, 1, 1, 12, 0, 0)
        db.session.add(User(id="usr_naive", email="n@example.com", created_at=naive, updated_at=naive))
        db.session.commit()
        db.session.expire_all()

        got = db.session.get(User, "usr_naive")
        assert got.created_at.tzinfo is not None
        assert got.created_at == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_an_aware_non_utc_value_is_converted_to_utc(self, db: Database):
        plus_five = timezone(timedelta(hours=5))
        aware = datetime(2026, 1, 1, 10, 0, 0, tzinfo=plus_five)
        db.session.add(User(id="usr_tz", email="tz@example.com", created_at=aware, updated_at=aware))
        db.session.commit()
        db.session.expire_all()

        got = db.session.get(User, "usr_tz")
        assert got.created_at == datetime(2026, 1, 1, 5, 0, 0, tzinfo=UTC)

    def test_expiry_comparisons_do_not_raise_across_the_boundary(self, db: Database):
        link = db.create_magic_link("compare@example.com")
        db.session.expire_all()
        reloaded = db.magic_link(link.token)
        # naive-vs-aware would raise TypeError here if the column let one through.
        assert (datetime.now(UTC) > reloaded.expires_at) in (True, False)


class TestSplitColumns:
    """`split_inputs` / `shares` are dataclass lists stored in one JSON column."""

    def test_split_input_list_round_trips_through_json(self):
        column = SplitInputList()
        rows = [SplitInput(member_id="mbr_1", raw=2.5), SplitInput(member_id="mbr_2", raw=None)]

        wire = column.process_bind_param(rows, dialect=None)
        assert wire == [
            {"member_id": "mbr_1", "raw": 2.5},
            {"member_id": "mbr_2", "raw": None},
        ]
        assert column.process_result_value(wire, dialect=None) == rows

    def test_expense_share_list_round_trips_through_json(self):
        column = ExpenseShareList()
        rows = [ExpenseShare(member_id="mbr_1", amount_minor=60), ExpenseShare(member_id="mbr_2", amount_minor=40)]

        wire = column.process_bind_param(rows, dialect=None)
        assert wire == [
            {"member_id": "mbr_1", "amount_minor": 60},
            {"member_id": "mbr_2", "amount_minor": 40},
        ]
        assert column.process_result_value(wire, dialect=None) == rows

    def test_a_missing_value_reads_back_as_an_empty_list(self):
        assert SplitInputList().process_result_value(None, dialect=None) == []
        assert ExpenseShareList().process_result_value(None, dialect=None) == []
        assert SplitInputList().process_bind_param(None, dialect=None) is None

    def test_splits_survive_a_real_write_and_reload(self, db: Database):
        user = make_user(db)
        group = db.add_group(
            Group(
                id=random_id("grp"),
                name="G",
                currency="USD",
                created_by_user_id=user.id,
                invite_code=db.unique_invite_code(),
                invite_token=random_id("tok"),
                invite_revoked=False,
                created_at=stamp(),
                updated_at=stamp(),
                version=1,
            )
        )
        expense = db.add_expense(
            Expense(
                id=random_id("exp"),
                group_id=group.id,
                description="Dinner",
                note="",
                amount_minor=100,
                date=datetime(2026, 1, 1).date(),
                payer_member_id="mbr_1",
                split_type="share",
                split_inputs=[SplitInput(member_id="mbr_1", raw=3.0)],
                shares=[ExpenseShare(member_id="mbr_1", amount_minor=100)],
                created_by_user_id=user.id,
                created_at=stamp(),
                updated_at=stamp(),
                version=1,
            )
        )
        db.session.expire_all()

        reloaded = db.expense(expense.id)
        assert reloaded.split_inputs == [SplitInput(member_id="mbr_1", raw=3.0)]
        assert reloaded.shares == [ExpenseShare(member_id="mbr_1", amount_minor=100)]


class TestSqlitePragmas:
    def test_foreign_keys_are_enforced(self, db: Database):
        orphan = Expense(
            id=random_id("exp"),
            group_id="grp_does_not_exist",
            description="x",
            note="",
            amount_minor=100,
            date=datetime(2026, 1, 1).date(),
            payer_member_id="mbr_1",
            split_type="equal",
            split_inputs=[],
            shares=[ExpenseShare(member_id="mbr_1", amount_minor=100)],
            created_by_user_id="usr_missing",
            created_at=stamp(),
            updated_at=stamp(),
            version=1,
        )
        db.session.add(orphan)
        with pytest.raises(IntegrityError):
            db.session.commit()


class TestIdentifiers:
    def test_random_id_carries_its_prefix_and_16_hex_chars(self):
        value = random_id("grp")
        assert re.fullmatch(r"grp_[0-9a-f]{16}", value)

    def test_invite_codes_are_grouped_and_use_the_unambiguous_alphabet(self):
        code = invite_code()
        assert re.fullmatch(r"[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{3}-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{3}-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{3}", code)
        assert not set("ILO01") & set(code.replace("-", ""))

    def test_high_entropy_tokens_are_hex_and_unique(self):
        tokens = {high_entropy_token() for _ in range(50)}
        assert len(tokens) == 50
        assert all(re.fullmatch(r"[0-9a-f]+", t) for t in tokens)

    def test_unique_invite_code_skips_a_code_already_in_use(self, db: Database, monkeypatch):
        user = make_user(db)
        taken = db.add_group(
            Group(
                id=random_id("grp"),
                name="G",
                currency="USD",
                created_by_user_id=user.id,
                invite_code="AAA-AAA-AAA",
                invite_token=random_id("tok"),
                invite_revoked=False,
                created_at=stamp(),
                updated_at=stamp(),
                version=1,
            )
        )
        codes = iter([taken.invite_code, "BBB-BBB-BBB"])
        monkeypatch.setattr("app.db.invite_code", lambda: next(codes))
        assert db.unique_invite_code() == "BBB-BBB-BBB"


class TestSessionLookup:
    def test_no_token_resolves_to_no_user(self, db: Database):
        assert db.user_for_session(None) is None
        assert db.user_for_session("") is None

    def test_an_unknown_token_resolves_to_no_user(self, db: Database):
        assert db.user_for_session("nonsense") is None

    def test_a_real_session_resolves_to_its_user(self, db: Database):
        user = make_user(db)
        session = db.create_session(user.id)
        assert db.user_for_session(session.token).id == user.id

    def test_deleting_a_session_is_idempotent(self, db: Database):
        user = make_user(db)
        session = db.create_session(user.id)
        db.delete_session(session.token)
        db.delete_session(session.token)  # already gone
        db.delete_session(None)
        assert db.user_for_session(session.token) is None


class TestInMemoryDatabase:
    def test_each_context_is_a_private_throwaway(self):
        with in_memory_database() as first:
            first.create_user("shared@example.com")
        with in_memory_database() as second:
            assert second.user_by_email("shared@example.com") is None

    def test_loaded_records_stay_readable_after_the_commit(self, db: Database):
        with db.transaction():
            user = db.create_user("after@example.com", display_name="After")
        # expire_on_commit=False: the endpoint builds its response from `user`
        # after the transaction closed, so this must not hit a detached error.
        assert user.display_name == "After"
