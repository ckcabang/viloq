"""The app in a real browser, against the running stack from `compose.yaml`.

`tests/integration/` speaks HTTP to the stack; these drive the page itself
through Chromium, so they also cover the frontend modules, the hash router, and
the views' wiring to `api.js`.

They are skipped unless `VILOQ_E2E_BASE_URL` names the stack, and need a
browser installed once with `uv run playwright install chromium`:

    docker compose up -d --build --wait
    VILOQ_E2E_BASE_URL=http://localhost:8000 uv run pytest tests/e2e

Like the integration tests, every run signs in with fresh addresses, since the
stack's database outlives it.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, ConsoleMessage, Page, expect

BASE_URL = os.environ.get("VILOQ_E2E_BASE_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not BASE_URL, reason="set VILOQ_E2E_BASE_URL to run against a running stack"
)


@pytest.fixture
def open_page(browser: Browser) -> Iterator:
    """A fresh browser profile per call, so each user has their own session.

    Fails the test on any console error: a module that 404s or throws shows up
    there and nowhere else.
    """
    contexts = []
    errors: list[str] = []

    def record(message: ConsoleMessage) -> None:
        if message.type == "error":
            errors.append(message.text)

    def new_page() -> Page:
        # Pin the locale: amounts are formatted with the browser's.
        context = browser.new_context(base_url=BASE_URL, locale="en-US")
        contexts.append(context)
        page = context.new_page()
        page.on("console", record)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        return page

    yield new_page
    for context in contexts:
        context.close()
    assert not errors, errors


def email(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:12]}@example.com"


def sign_in(page: Page, address: str, display_name: str) -> None:
    """Request a magic link, follow the one the page shows, and pick a name.

    Starts from whatever sign-in screen `page` is on.
    """
    page.get_by_label("Email address").fill(address)
    page.get_by_role("button", name="Email me a link").click()
    page.get_by_role("link", name="Open my magic link").click()
    page.get_by_label("Display name").fill(display_name)
    page.get_by_role("button", name="Continue").click()


def balance(page: Page, name: str):
    return page.locator(".balances__row", has_text=name)


class TestSignIn:
    def test_a_new_user_lands_on_an_empty_group_list(self, open_page):
        page = open_page()
        page.goto("/")
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()

        sign_in(page, email("alice"), "Alice")

        expect(page.get_by_role("heading", name="Your groups")).to_be_visible()
        expect(page.get_by_text("You're not in any groups yet.")).to_be_visible()


class TestSplittingAndSettling:
    def test_invite_split_settle(self, open_page):
        # Alice signs in and creates a group.
        alice = open_page()
        alice.goto("/")
        sign_in(alice, email("alice"), "Alice")
        alice.get_by_role("link", name="New group", exact=True).click()
        alice.get_by_label("Group name").fill("Lisbon trip")
        alice.get_by_label("Currency").select_option("EUR")
        alice.get_by_role("button", name="Create group").click()
        expect(alice.get_by_role("heading", name="Lisbon trip")).to_be_visible()

        # She reads the invite code off the invite dialog.
        alice.get_by_role("button", name="Invite").click()
        code = alice.locator("[data-code]").input_value()
        assert re.fullmatch(r"[A-Z0-9]{3}-[A-Z0-9]{3}-[A-Z0-9]{3}", code)
        alice.locator("[data-close]").click()

        # Bob follows the invite in his own browser, signing in on the way.
        bob = open_page()
        bob.goto(f"/#/join?code={code}")
        expect(bob.get_by_role("heading", name='Join "Lisbon trip"')).to_be_visible()
        sign_in(bob, email("bob"), "Bob")
        bob.get_by_role("button", name="Join group").click()
        expect(bob.get_by_role("heading", name="Lisbon trip")).to_be_visible()

        # Alice pays for dinner, split equally; the form previews the split.
        alice.reload()
        alice.get_by_role("link", name="Add expense").click()
        alice.get_by_label("Description").fill("Dinner")
        alice.get_by_label("Amount (EUR)").fill("90.00")
        expect(alice.locator("[data-alloc]")).to_contain_text("reconciles exactly")
        alice.get_by_role("button", name="Add expense").click()

        expect(alice.locator(".txlist__row", has_text="Dinner")).to_contain_text("€90.00")
        expect(balance(alice, "Alice")).to_contain_text("is owed €45.00")
        expect(balance(alice, "Bob")).to_contain_text("owes €45.00")
        expect(alice.locator(".transfers__row")).to_contain_text("Bob → Alice")

        # Bob sees he owes, and records paying it back from the suggestion.
        bob.reload()
        expect(balance(bob, "Bob")).to_contain_text("owes €45.00")
        bob.locator(".transfers__row").get_by_role("link", name="Record").click()
        bob.get_by_role("button", name="Record payment").click()

        expect(bob.get_by_text("Everyone is settled up.")).to_be_visible()
        alice.reload()
        expect(balance(alice, "Alice")).to_contain_text("settled")
        expect(balance(alice, "Bob")).to_contain_text("settled")
