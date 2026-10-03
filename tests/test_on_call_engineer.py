"""`on-call-engineer/poll.py` — which alerts reach the agent, once each, and
what it is told.

Grafana is a stub HTTP server, the agent a Python one-liner that echoes its
prompt and commits, and the repository a throwaway one: nothing here calls a
real agent or touches this checkout.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from backend.config import REPO_ROOT

_spec = importlib.util.spec_from_file_location(
    "on_call_poll", REPO_ROOT / "on-call-engineer" / "poll.py"
)
assert _spec and _spec.loader
poll = importlib.util.module_from_spec(_spec)
# Its dataclasses look their module up here while it loads.
sys.modules[_spec.name] = poll
_spec.loader.exec_module(poll)

TAG = "20261003-120000-83a1bcf"

# Echoes the prompt, as its report, and commits a file, as a fix would.
FAKE_AGENT = (
    sys.executable,
    "-c",
    "import subprocess, sys; prompt = sys.stdin.read(); print(prompt);"
    " open('fix.txt', 'w').write('fixed');"
    " subprocess.run(['git', 'add', 'fix.txt'], check=True);"
    " subprocess.run(['git', 'commit', '-qm', 'Fix it'], check=True)",
)


def firing(fingerprint: str = "a1d50d47a4609f54", starts: str = "2026-10-03T19:53:30Z"):
    return {
        "fingerprint": fingerprint,
        "startsAt": starts,
        "status": {"state": "active"},
        "generatorURL": "http://localhost:3000/alerting/grafana/x/view",
        "labels": {
            "alertname": "ViloqServerErrors",
            "service": "viloq",
            "environment": "production",
            "version": TAG,
            "owner": "ckcabang",
            "__alert_rule_uid__": "x",
        },
        "annotations": {
            "summary": "viloq production: 12% of requests failing with a 5xx",
            "__values__": "{}",
        },
    }


class Grafana:
    """Serves `alerts` as Grafana's Alertmanager would, and keeps the requests."""

    def __init__(self) -> None:
        self.alerts: list[dict] = []
        self.requests: list[tuple[str, dict]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                url = urlparse(self.path)
                stub.requests.append((url.path, dict(self.headers)))
                stub.queries.append(parse_qs(url.query))
                body = json.dumps(stub.alerts).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self.queries: list[dict] = []
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def grafana() -> Iterator[Grafana]:
    stub = Grafana()
    yield stub
    stub.server.shutdown()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository with a `main`, standing in for this one."""
    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "On Call")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "on-call@example.com")
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("app\n")
    git(root, "add", "README.md")
    git(root, "commit", "-qm", "Start")
    monkeypatch.setattr(poll, "REPO_ROOT", root)
    return root


@pytest.fixture
def settings(tmp_path: Path, grafana: Grafana, repo: Path):
    instructions = tmp_path / "instructions.md"
    instructions.write_text("You are the on-call engineer.\nFix it.\n")
    return poll.Settings(
        grafana_url=grafana.url,
        token=None,
        filters=('service="viloq"',),
        agent=FAKE_AGENT,
        instructions=instructions,
        base="main",
        worktrees=tmp_path / "worktrees",
        reports=tmp_path / "reports",
        state=tmp_path / "state.json",
        max_agents=1,
    )


def run_once(settings) -> poll.OnCall:
    on_call = poll.OnCall(settings)
    on_call.load()
    on_call.poll()
    on_call.wait()
    return on_call


class TestFetch:
    def test_asks_for_active_unsilenced_alerts_matching_the_filters(
        self, grafana: Grafana, settings
    ):
        poll.fetch_alerts(settings)
        ((path, headers),) = grafana.requests
        assert path == poll.ALERTS_PATH
        assert "Authorization" not in headers
        (query,) = grafana.queries
        assert query["active"] == ["true"]
        assert query["silenced"] == ["false"]
        assert query["inhibited"] == ["false"]
        assert query["filter"] == ['service="viloq"']

    def test_sends_the_token_when_there_is_one(self, grafana: Grafana, settings):
        poll.fetch_alerts(replace(settings, token="glsa_x"))
        ((_, headers),) = grafana.requests
        assert headers["Authorization"] == "Bearer glsa_x"

    def test_leaves_out_alerts_no_longer_active(self, grafana: Grafana, settings):
        suppressed = firing("b" * 16)
        suppressed["status"] = {"state": "suppressed"}
        grafana.alerts = [firing(), suppressed]
        assert [a["fingerprint"] for a in poll.fetch_alerts(settings)] == [
            firing()["fingerprint"]
        ]


    def test_leaves_out_alerts_the_filters_do_not_match(
        self, grafana: Grafana, settings
    ):
        # As Grafana does when it cannot parse a filter: it sends everything.
        other = firing("c" * 16)
        other["labels"]["service"] = "billing"
        grafana.alerts = [firing(), other]
        assert [a["fingerprint"] for a in poll.fetch_alerts(settings)] == [
            firing()["fingerprint"]
        ]


class TestHandOver:
    def test_a_new_alert_gets_an_agent_on_its_own_branch(
        self, grafana: Grafana, settings, repo: Path
    ):
        grafana.alerts = [firing()]
        run_once(settings)
        (branch,) = git(repo, "branch", "--list", "on-call/*", "--format=%(refname:short)").split()
        assert git(repo, "log", "--format=%s", f"main..{branch}").split("\n")[0] == "Fix it"
        # Its fix stays off main, and out of the checkout.
        assert git(repo, "log", "--format=%s", "main").strip() == "Start"
        assert not (repo / "fix.txt").exists()
        (report,) = settings.reports.glob("*.md")
        prompt = report.read_text(encoding="utf-8")
        assert prompt.startswith("You are the on-call engineer.\nFix it.\n")
        assert f"on branch `{branch}`" in prompt

    def test_each_firing_is_handed_over_once(self, grafana: Grafana, settings):
        grafana.alerts = [firing()]
        run_once(settings)
        run_once(settings)  # a restart, still firing
        assert len(list(settings.reports.glob("*.md"))) == 1

    def test_firing_again_after_resolving_is_new(self, grafana: Grafana, settings):
        grafana.alerts = [firing()]
        run_once(settings)
        grafana.alerts = [firing(starts="2026-10-03T21:00:00Z")]
        run_once(settings)
        assert len(list(settings.reports.glob("*.md"))) == 2

    def test_an_unreachable_grafana_is_logged_and_retried(
        self, settings, capsys: pytest.CaptureFixture
    ):
        on_call = poll.OnCall(replace(settings, grafana_url="http://127.0.0.1:1"))
        on_call.poll()
        assert "could not fetch alerts" in capsys.readouterr().out
        assert not on_call.handled


class TestPrompt:
    def prompt(self) -> str:
        return poll.prompt_for(firing(), "Do the thing.\n", "on-call/x", "main")

    def test_the_instructions_come_first_and_whole(self):
        assert self.prompt().startswith("Do the thing.\n\n")

    def test_the_alert_is_marked_as_data(self):
        prompt = self.prompt()
        alert = json.loads(prompt.split("<alert>")[1].split("</alert>")[0])
        assert alert["labels"]["version"] == TAG
        assert alert["annotations"]["summary"].startswith("viloq production")
        assert "__alert_rule_uid__" not in alert["labels"]
        assert "__values__" not in alert["annotations"]
        assert "not instructions" in prompt

    def test_names_the_commit_the_version_was_built_from(self):
        assert "commit 83a1bcf" in self.prompt()
        assert poll.deployed_commit(TAG) == "83a1bcf"
        assert poll.deployed_commit(None) is None
        assert poll.deployed_commit("latest") is None

    def test_the_instructions_file_is_the_one_given_to_the_agent(self):
        text = (REPO_ROOT / "on-call-engineer" / "instructions.md").read_text()
        assert text.startswith("You are the on-call engineer for this repository.")
