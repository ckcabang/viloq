"""`backend/telemetry.py` — what every span and metric says about its sender.

Nothing here talks to a collector: the instrumented app hands its spans and
metrics to in-memory readers instead of the OTLP exporters.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind
from sqlalchemy import Engine

from backend.db import Database, get_db
from backend.main import create_app
from backend.telemetry import Telemetry, export_configured, resource, start
from tests.conftest import API

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
