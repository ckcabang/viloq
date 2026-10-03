"""`backend/telemetry.py` — what every span and metric says about its sender.

Nothing here talks to a collector: the instrumented app hands its spans and
metrics to in-memory readers instead of the OTLP exporters.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics.export import Histogram, InMemoryMetricReader, Sum
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind
from sqlalchemy import Engine

from backend.config import REPO_ROOT
from backend.db import Database, get_db
from backend.main import create_app
from backend.telemetry import Telemetry, export_configured, resource, start
from tests.conftest import API, Actor, GroupCtx, join, make_group, sign_in

IMAGE_TAG = "20260818-163457-83242da"
COMMIT = "83242da" + "0" * 33

_BUILD_AND_OTEL_VARS = (
    "VILOQ_ENVIRONMENT",
    "VILOQ_IMAGE_TAG",
    "VILOQ_COMMIT",
    "OTEL_SERVICE_NAME",
    "OTEL_RESOURCE_ATTRIBUTES",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_SDK_DISABLED",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from a local build, whatever the shell running the tests has set."""
    for name in _BUILD_AND_OTEL_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def deployed(monkeypatch: pytest.MonkeyPatch) -> None:
    """As on Render: CI baked in the build, `render.yaml` names the environment."""
    monkeypatch.setenv("VILOQ_ENVIRONMENT", "production")
    monkeypatch.setenv("VILOQ_IMAGE_TAG", IMAGE_TAG)
    monkeypatch.setenv("VILOQ_COMMIT", COMMIT)


class TestResource:
    def test_names_the_service_its_environment_and_version(self, deployed):
        attributes = resource().attributes
        assert attributes["service.name"] == "viloq"
        assert attributes["deployment.environment.name"] == "production"
        assert attributes["service.version"] == IMAGE_TAG
        assert attributes["vcs.ref.head.revision"] == COMMIT

    def test_a_local_build_leaves_out_what_it_does_not_know(self):
        attributes = resource().attributes
        assert attributes["service.name"] == "viloq"
        assert "deployment.environment.name" not in attributes
        assert "service.version" not in attributes
        assert "vcs.ref.head.revision" not in attributes

    def test_the_standard_otel_variables_have_the_last_word(
        self, deployed, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("OTEL_SERVICE_NAME", "viloq-canary")
        monkeypatch.setenv(
            "OTEL_RESOURCE_ATTRIBUTES",
            "deployment.environment.name=staging,team=payments",
        )
        attributes = resource().attributes
        assert attributes["service.name"] == "viloq-canary"
        assert attributes["deployment.environment.name"] == "staging"
        assert attributes["team"] == "payments"
        assert attributes["service.version"] == IMAGE_TAG


class TestExport:
    def test_off_without_an_endpoint(self):
        assert not export_configured()
        assert start() is None

    def test_on_with_an_endpoint(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
        assert export_configured()

    def test_the_sdk_switch_turns_it_off(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
        monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
        assert not export_configured()

    def test_the_app_is_uninstrumented_without_one(self):
        assert create_app().state.telemetry is None


@dataclass
class Captured:
    client: TestClient
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader

    def finished(self) -> list[ReadableSpan]:
        return list(self.spans.get_finished_spans())

    def points(self, name: str) -> list:
        """The data points of metric `name` collected so far."""
        data = self.metrics.get_metrics_data()
        return [
            point
            for batch in (data.resource_metrics if data else [])
            for scope in batch.scope_metrics
            for metric in scope.metrics
            if metric.name == name
            for point in metric.data.data_points
        ]

    def counted(self, name: str) -> dict[tuple, int]:
        """Counter `name`'s totals so far, keyed by their sorted attributes,
        leaving out the series still at the zero they start at."""
        return {
            tuple(sorted(point.attributes.items())): point.value
            for point in self.points(name)
            if point.value
        }


@pytest.fixture
def captured(deployed, db: Database, test_engine: Engine) -> Iterator[Captured]:
    """The app, instrumented as `start` would, but recording to memory."""
    spans, metrics = InMemorySpanExporter(), InMemoryMetricReader()
    telemetry = Telemetry.sending_to(SimpleSpanProcessor(spans), metrics)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    telemetry.instrument(app, test_engine)
    try:
        with TestClient(app) as client:
            yield Captured(client, spans, metrics)
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        SQLAlchemyInstrumentor().uninstrument()
        telemetry.shutdown()


class TestInstrumentedApp:
    def test_a_request_is_traced_with_its_queries(self, captured: Captured):
        response = captured.client.post(
            f"{API}/auth/magic-links", json={"email": "alice@example.com"}
        )
        assert response.status_code == 200

        spans = captured.finished()
        (request,) = [span for span in spans if span.kind is SpanKind.SERVER]
        assert request.name == f"POST {API}/auth/magic-links"
        assert request.attributes["http.status_code"] == 200

        queries = [span for span in spans if span.attributes.get("db.system")]
        assert any(span.name.startswith("INSERT") for span in queries)
        for query in queries:
            assert query.parent is not None
            assert query.context.trace_id == request.context.trace_id

    def test_every_span_names_its_sender(self, captured: Captured):
        captured.client.get(f"{API}/auth/me")
        spans = captured.finished()
        assert spans
        for span in spans:
            attributes = span.resource.attributes
            assert attributes["service.name"] == "viloq"
            assert attributes["deployment.environment.name"] == "production"
            assert attributes["service.version"] == IMAGE_TAG

    def test_so_does_every_metric(self, captured: Captured):
        captured.client.get(f"{API}/auth/me")
        data = captured.metrics.get_metrics_data()
        names = set()
        for batch in data.resource_metrics:
            attributes = batch.resource.attributes
            assert attributes["service.name"] == "viloq"
            assert attributes["deployment.environment.name"] == "production"
            assert attributes["service.version"] == IMAGE_TAG
            names |= {m.name for scope in batch.scope_metrics for m in scope.metrics}
        assert "http.server.duration" in names

    def test_health_checks_leave_no_trace(self, captured: Captured):
        # Render polls it every few seconds; neither the request nor its
        # `SELECT 1` should reach the backend.
        assert captured.client.get("/healthz").status_code == 200
        assert captured.finished() == []


def _expense(
    group: GroupCtx, payer: Actor, split_type: str = "equal", raws=None
) -> dict:
    """A 90.00 expense split across all of `group`, `raws` per member if given."""
    return {
        "description": "Dinner",
        "date": "2026-09-02",
        "amountMinor": 9000,
        "payerMemberId": group.members[payer.email],
        "splitType": split_type,
        "participants": [
            {"memberId": member, **({"raw": raws[i]} if raws else {})}
            for i, member in enumerate(group.members.values())
        ],
    }


class TestAppMetrics:
    def test_every_counted_series_is_there_from_the_start(self, captured: Captured):
        # Prometheus's `increase` needs a sample before a count to see it: a
        # series that first appeared at 1 would lose the first group created
        # after each deploy.
        expenses = captured.points("viloq.expense.changes")
        assert len(expenses) == 3 * 4  # operations by split types
        assert {point.value for point in expenses} == {0}
        for name, series in {
            "viloq.magic_links.requested": 1,
            "viloq.sign_ins": 5,
            "viloq.groups.created": 1,
            "viloq.members.joined": 1,
            "viloq.payment.changes": 3,
            "viloq.settlements.suggested": 2,
        }.items():
            assert [point.value for point in captured.points(name)] == [0] * series

        # Every code on each of the contract's routes, and the unmatched path.
        errors = captured.points("viloq.api.errors")
        assert {point.value for point in errors} == {0}
        routes = {point.attributes.get("http.route") for point in errors}
        assert len(routes) == 15 + 1
        assert f"{API}/groups/{{groupId}}/expenses/{{expenseId}}" in routes
        assert "/healthz" not in routes
        assert len(errors) == 15 * 11 + 1

    def test_sign_ins_say_who_got_in_and_why_the_rest_did_not(
        self, captured: Captured, db: Database
    ):
        client = captured.client

        def request_link(email: str) -> str:
            response = client.post(f"{API}/auth/magic-links", json={"email": email})
            return response.json()["token"]

        def verify(token: str) -> int:
            response = client.post(
                f"{API}/auth/magic-links/verify", json={"token": token}
            )
            return response.status_code

        sign_in(client, "alice@example.com")
        sign_in(client, "alice@example.com")
        used = request_link("bob@example.com")
        assert verify(used) == 200
        assert verify(used) == 400
        assert verify("no-such-token") == 400
        expired = request_link("sam@example.com")
        db.magic_link(expired).expires_at -= timedelta(minutes=16)
        assert verify(expired) == 400

        assert captured.counted("viloq.sign_ins") == {
            (("viloq.sign_in.result", "signed_up"),): 2,
            (("viloq.sign_in.result", "signed_in"),): 1,
            (("viloq.sign_in.result", "used_link"),): 1,
            (("viloq.sign_in.result", "unknown_link"),): 1,
            (("viloq.sign_in.result", "expired_link"),): 1,
        }
        assert captured.counted("viloq.magic_links.requested") == {(): 4}

    def test_group_activity_is_counted_once_it_has_happened(
        self, captured: Captured
    ):
        client = captured.client
        alice = sign_in(client, "alice@example.com")
        bob = sign_in(client, "bob@example.com")
        group = make_group(alice)
        join(group, bob, "Bob")
        join(group, bob, "Bob")  # already in: nothing changes, nothing counted

        expenses = f"/groups/{group.id}/expenses"
        created = alice.post(expenses, json=_expense(group, alice)).json()
        updated = alice.put(
            f"{expenses}/{created['id']}",
            json=_expense(group, alice, "percentage", [60, 40]),
            headers=alice.if_match(created["version"]),
        ).json()
        deleted = alice.delete(
            f"{expenses}/{created['id']}", headers=alice.if_match(updated["version"])
        )
        assert deleted.status_code == 204
        alice.post(expenses, json=_expense(group, alice))

        payment = alice.post(
            f"/groups/{group.id}/payments",
            json={
                "recipientMemberId": group.members[bob.email],
                "amountMinor": 4500,
                "date": "2026-09-03",
            },
        ).json()
        alice.delete(
            f"/groups/{group.id}/payments/{payment['id']}",
            headers=alice.if_match(payment["version"]),
        )

        alice.get(f"/groups/{group.id}/settlement")
        alice.get(f"/groups/{group.id}/settlement?strategy=relationship")
        alice.get(f"/groups/{group.id}")

        assert captured.counted("viloq.groups.created") == {(): 1}
        assert captured.counted("viloq.members.joined") == {(): 1}
        assert captured.counted("viloq.expense.changes") == {
            (("viloq.operation", "create"), ("viloq.split_type", "equal")): 2,
            (("viloq.operation", "update"), ("viloq.split_type", "percentage")): 1,
            (("viloq.operation", "delete"), ("viloq.split_type", "percentage")): 1,
        }
        assert captured.counted("viloq.payment.changes") == {
            (("viloq.operation", "create"),): 1,
            (("viloq.operation", "delete"),): 1,
        }
        assert captured.counted("viloq.settlements.suggested") == {
            (("viloq.settlement.strategy", "minimized"),): 1,
            (("viloq.settlement.strategy", "relationship"),): 1,
        }
        # `make_group` loads the group while it is empty; the last load sees
        # the one expense left.
        (loads,) = captured.points("viloq.group.expenses")
        assert (loads.count, loads.min, loads.max) == (2, 0, 1)

    def test_a_refused_change_is_not_counted(self, captured: Captured):
        alice = sign_in(captured.client, "alice@example.com")
        group = make_group(alice)
        refused = alice.post(
            f"/groups/{group.id}/expenses", json=_expense(group, alice, "exact", [1])
        )
        assert refused.status_code == 400
        assert captured.counted("viloq.expense.changes") == {}

    def test_errors_are_counted_by_contract_code_and_route(
        self, captured: Captured
    ):
        client = captured.client
        alice = sign_in(client, "alice@example.com")
        group = make_group(alice)
        rename = {"name": "Porto"}
        alice.patch(f"/groups/{group.id}", json=rename, headers=alice.if_match(99))
        alice.patch(f"/groups/{group.id}", json=rename)
        alice.post("/groups", json={"currency": "XYZ"})
        client.get(f"{API}/groups")
        client.get(f"{API}/no-such-thing")

        one_group = f"{API}/groups/{{groupId}}"
        assert captured.counted("viloq.api.errors") == {
            (("error.type", "version_conflict"), ("http.route", one_group)): 1,
            (("error.type", "precondition_required"), ("http.route", one_group)): 1,
            (("error.type", "validation"), ("http.route", f"{API}/groups")): 1,
            (("error.type", "unauthorized"), ("http.route", f"{API}/groups")): 1,
            # No route matched, so none is named.
            (("error.type", "not_found"),): 1,
        }

    def test_they_name_the_environment_and_version_that_sent_them(
        self, captured: Captured
    ):
        sign_in(captured.client, "alice@example.com")
        data = captured.metrics.get_metrics_data()
        (batch,) = [
            batch
            for batch in data.resource_metrics
            for scope in batch.scope_metrics
            if scope.scope.name == "viloq"
        ]
        attributes = batch.resource.attributes
        assert attributes["deployment.environment.name"] == "production"
        assert attributes["service.version"] == IMAGE_TAG
        assert attributes["vcs.ref.head.revision"] == COMMIT


DASHBOARD = REPO_ROOT / "observability" / "dashboards" / "viloq.json"
ALERTS = REPO_ROOT / "observability" / "alerts" / "viloq.yaml"
FILTER = 'deployment_environment_name=~"$environment",service_version=~"$version"'
# A metric and its selector, `viloq_..._total{...}`; a bare `viloq_...` is a
# label. The app's own metrics, and the HTTP ones the instrumentation records.
_SELECTED = re.compile(r"\b((?:viloq|http_server)_\w+)(\{[^}]*\})")
_SERIES_ENDINGS = ("_total", "_bucket", "_count", "_sum")
# The unit suffixes Prometheus adds; a `{...}` unit, like `{group}`, adds none.
_UNIT_SUFFIXES = {"ms": "_milliseconds", "s": "_seconds", "By": "_bytes"}


def _prometheus_names(captured: Captured) -> set[str]:
    """What Prometheus calls the app's metrics: `.` becomes `_`, the unit is
    spelled out after it, a counter gets `_total`, and a histogram is its
    `_bucket`, `_count` and `_sum` series."""
    data = captured.metrics.get_metrics_data()
    names = set()
    for batch in data.resource_metrics:
        for scope in batch.scope_metrics:
            for metric in scope.metrics:
                base = metric.name.replace(".", "_")
                base += _UNIT_SUFFIXES.get(metric.unit or "", "")
                if isinstance(metric.data, Histogram):
                    names |= {f"{base}_bucket", f"{base}_count", f"{base}_sum"}
                elif isinstance(metric.data, Sum) and metric.data.is_monotonic:
                    names.add(f"{base}_total")
                else:
                    names.add(base)
    return names


def _dashboard() -> dict:
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


class TestDashboard:
    def queries(self) -> list[str]:
        return [
            target["expr"]
            for panel in _dashboard()["panels"]
            for target in panel.get("targets", [])
        ]

    def test_every_query_filters_by_environment_and_version(self):
        for query in self.queries():
            selectors = _SELECTED.findall(query)
            assert selectors, query
            for _, selector in selectors:
                assert FILTER in selector, query
            # A metric named with no selector at all would not be filtered.
            bare = re.findall(r"\b(?:viloq|http_server)_\w+(?![\w{])", query)
            assert not [name for name in bare if name.endswith(_SERIES_ENDINGS)], query

    def test_it_asks_only_for_metrics_the_app_sends(self, captured: Captured):
        alice = sign_in(captured.client, "alice@example.com")
        make_group(alice)  # loads the group, so the histogram has a point
        sent = _prometheus_names(captured)
        asked = {
            name for query in self.queries() for name, _ in _SELECTED.findall(query)
        }
        assert asked <= sent


class TestAlerts:
    """`observability/alerts/viloq.yaml`, read without a YAML parser."""

    def rules(self) -> str:
        return ALERTS.read_text(encoding="utf-8")

    def field(self, name: str) -> str:
        (value,) = re.findall(rf"^ +{name}: (.+)$", self.rules(), re.MULTILINE)
        return value.strip('"')

    def test_it_watches_production_only(self):
        selectors = _SELECTED.findall(self.rules())
        assert selectors
        for _, selector in selectors:
            assert 'service_name="viloq"' in selector
            assert 'deployment_environment_name="production"' in selector

    def test_it_asks_only_for_metrics_the_app_sends(self, captured: Captured):
        sign_in(captured.client, "alice@example.com")
        sent = _prometheus_names(captured)
        asked = {name for name, _ in _SELECTED.findall(self.rules())}
        assert asked <= sent

    def test_it_says_what_is_failing_where_and_whose_it_is(self):
        assert self.field("service") == "{{ $labels.service_name }}"
        assert self.field("environment") == "{{ $labels.deployment_environment_name }}"
        assert self.field("version") == "{{ $labels.service_version }}"
        assert self.field("owner")
        assert self.field("severity") == "critical"

    def test_it_links_to_the_panel_that_charts_it(self):
        board = _dashboard()
        assert self.field("__dashboardUid__") == board["uid"]
        (panel,) = [
            panel
            for panel in board["panels"]
            if str(panel["id"]) == self.field("__panelId__")
        ]
        assert "http_server_duration_milliseconds_count" in panel["targets"][0]["expr"]
        url = re.search(r"dashboard_url: >-\s+(.+)$", self.rules(), re.MULTILINE)
        assert url is not None
        # Grafana's external URL ends in `/`.
        assert url[1].startswith("{{ externalURL }}d/" + board["uid"] + "/")
