"""Two requests racing to change one record.

`app/db.py` claims that a read-modify-write wrapped in `transaction()` is atomic:
on SQLite it takes the write lock at `BEGIN IMMEDIATE`, so two edits of the same
row cannot interleave and lose an update. That guarantee needs real connections,
so these tests run against a file-backed database and the app's own per-request
session factory rather than the shared in-memory fixture.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from app.db import Database, create_db_engine, get_db, new_session_factory
from app.main import app as fastapi_app
from app.models import Base
from tests.conftest import Actor, sign_in


@pytest.fixture
def racing_clients(tmp_path) -> Iterator[Callable[[], TestClient]]:
    """A factory for TestClients that all share one file-backed database.

    Each racing branch needs its own client: `httpx.Client` (which `TestClient`
    wraps) is not safe to call concurrently from multiple threads, so sharing
    one would risk transport-state errors unrelated to the DB race under test.
    """
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'race.db'}")
    Base.metadata.create_all(engine)
    sessions = new_session_factory(engine)

    def override() -> Iterator[Database]:
        with sessions() as session:
            yield Database(session)

    fastapi_app.dependency_overrides[get_db] = override
    stack = ExitStack()

    def make_client() -> TestClient:
        return stack.enter_context(TestClient(fastapi_app))

    try:
        yield make_client
    finally:
        stack.close()
        fastapi_app.dependency_overrides.clear()
        engine.dispose()


def run_together(*targets) -> None:
    """Start every callable at once and wait for all of them.

    An exception raised in a worker thread is captured and re-raised here, so a
    failing racing request surfaces as itself rather than as a downstream
    KeyError when the test reads a result the thread never recorded.
    """
    ready = threading.Barrier(len(targets))
    errors: list[BaseException] = []
    lock = threading.Lock()

    def wrapped(fn):
        def go() -> None:
            ready.wait()
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                with lock:
                    errors.append(exc)

        return go

    threads = [threading.Thread(target=wrapped(fn)) for fn in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


class TestRacingEdits:
    def test_one_patch_wins_and_the_other_gets_a_conflict(self, racing_clients):
        alice = sign_in(racing_clients(), "alice@example.com")
        group = alice.post(
            "/groups", json={"name": "Race", "currency": "USD", "displayName": "Alice"}
        ).json()

        results: dict[str, int] = {}

        def edit(tag: str, name: str):
            # A separate client per branch: two threads must not share one.
            editor = sign_in(racing_clients(), "alice@example.com")

            def go() -> None:
                response = editor.patch(
                    f"/groups/{group['id']}",
                    json={"name": name},
                    headers=editor.if_match(1),
                )
                results[tag] = response.status_code

            return go

        run_together(edit("first", "A"), edit("second", "B"))

        assert sorted(results.values()) == [200, 409], results
        # Exactly one edit landed: the version moved 1 -> 2, never 1 -> 3.
        final = alice.get(f"/groups/{group['id']}").json()["group"]
        assert final["version"] == 2
        assert final["name"] in {"A", "B"}

    def test_a_delete_and_an_edit_do_not_both_apply(self, racing_clients):
        alice = sign_in(racing_clients(), "alice@example.com")
        group = alice.post(
            "/groups", json={"name": "Race", "currency": "USD", "displayName": "Alice"}
        ).json()
        me = alice.get(f"/groups/{group['id']}").json()["me"]["memberId"]
        expense = alice.post(
            f"/groups/{group['id']}/expenses",
            json={
                "description": "Dinner",
                "date": "2026-09-02",
                "amountMinor": 5000,
                "payerMemberId": me,
                "splitType": "equal",
                "participants": [{"memberId": me}],
            },
        ).json()

        results: dict[str, int] = {}
        # A separate client per branch: two threads must not share one.
        deleter = sign_in(racing_clients(), "alice@example.com")
        editor = sign_in(racing_clients(), "alice@example.com")

        def delete() -> None:
            results["delete"] = deleter.delete(
                f"/groups/{group['id']}/expenses/{expense['id']}",
                headers=deleter.if_match(1),
            ).status_code

        def edit() -> None:
            results["edit"] = editor.put(
                f"/groups/{group['id']}/expenses/{expense['id']}",
                json={
                    "description": "Brunch",
                    "date": "2026-09-02",
                    "amountMinor": 5000,
                    "payerMemberId": me,
                    "splitType": "equal",
                    "participants": [{"memberId": me}],
                },
                headers=editor.if_match(1),
            ).status_code

        run_together(delete, edit)

        # Whichever ran first wins (204 for the delete, 200 for the edit); the
        # other sees a stale version (409) or a row already gone (404). Exactly
        # one may succeed — both succeeding is the lost-update bug.
        succeeded = [results["delete"] == 204, results["edit"] == 200]
        assert succeeded.count(True) == 1, results
        assert set(results.values()) <= {200, 204, 404, 409}, results
