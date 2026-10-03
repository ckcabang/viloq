"""OpenTelemetry: a trace for every request, with a span for every query it
makes, plus HTTP and connection-pool metrics, all sent over OTLP/HTTP.

Every span and metric carries the resource built in `resource`: the service
name, the environment, and the version (the image tag CI built) that sent it.

Nothing is sent unless `OTEL_EXPORTER_OTLP_ENDPOINT` names a collector, or a
vendor's OTLP endpoint (whose API key then goes in
`OTEL_EXPORTER_OTLP_HEADERS`). Without one, as in local development and the
tests, the app runs uninstrumented rather than retrying `localhost:4318`
forever. The SDK's other `OTEL_*` variables apply as documented:
`OTEL_SDK_DISABLED=true` turns it all off, and `OTEL_SERVICE_NAME` or
`OTEL_RESOURCE_ATTRIBUTES` override what `resource` sets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import OTELResourceDetector, Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind
from sqlalchemy import Engine

from backend.config import deployed_commit, deployed_image, deployment_environment

SERVICE_NAME = "viloq"

# Render polls it every few seconds; tracing it would bury the real requests.
_UNTRACED_URLS = "/healthz"

# The ASGI middleware's spans for each chunk received and sent, three or more
# per request, which say little the request's own span does not.
_UNTRACED_ASGI_EVENTS = ["receive", "send"]


def resource() -> Resource:
    """Who is sending: the service, its environment, and the build it runs.

    The attribute names are OpenTelemetry's semantic conventions, so a backend
    can group and filter by them without any mapping. Unknown values (a local
    build has no image tag, and no environment unless one is set) are left
    out rather than guessed.
    """
    attributes = {"service.name": SERVICE_NAME}
    known = {
        "deployment.environment.name": deployment_environment(),
        # The image tag, `YYYYMMDD-HHMMSS-shortsha`: what `/healthz` reports,
        # what deploys move between environments, and unique per build.
        "service.version": deployed_image(),
        "vcs.ref.head.revision": deployed_commit(),
    }
    attributes.update({key: value for key, value in known.items() if value})
    # `Resource.create` adds the SDK's own attributes but lets ours win over
    # the environment's; merging the environment's on top reverses that.
    return Resource.create(attributes).merge(OTELResourceDetector().detect())


def export_configured() -> bool:
    """Whether there is somewhere to send telemetry, and it is not turned off."""
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return False
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())


@dataclass
class Telemetry:
    """The providers that spans and metrics go through, sharing one resource."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider

    @classmethod
    def sending_to(cls, spans: SpanProcessor, reader: MetricReader) -> Telemetry:
        shared = resource()
        tracer_provider = TracerProvider(resource=shared)
        tracer_provider.add_span_processor(_DropStrayQueries(spans))
        return cls(
            tracer_provider=tracer_provider,
            meter_provider=MeterProvider(resource=shared, metric_readers=[reader]),
        )

    def instrument(self, app: FastAPI, engine: Engine) -> None:
        """Trace and measure every request to `app` and every query on `engine`."""
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=self.tracer_provider,
            meter_provider=self.meter_provider,
            excluded_urls=_UNTRACED_URLS,
            exclude_spans=_UNTRACED_ASGI_EVENTS,
        )
        SQLAlchemyInstrumentor().instrument(
            engine=engine,
            tracer_provider=self.tracer_provider,
            meter_provider=self.meter_provider,
        )

    def shutdown(self) -> None:
        """Send whatever is still buffered; at exit, so the last of it is not lost."""
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()


class _DropStrayQueries(SpanProcessor):
    """Passes spans on to `inner`, except queries that ran outside any request.

    A query is worth a span as a step in the request that made it. With no
    request around it, it is the `SELECT 1` of an untraced `/healthz`, or the
    table check at startup. Dropping these here, rather than suppressing
    instrumentation while they run, leaves the connection-pool metrics whole;
    those count every checkout and checkin, and would drift if they missed one.
    """

    def __init__(self, inner: SpanProcessor) -> None:
        self._inner = inner

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        self._inner.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        if span.parent is None and span.kind is SpanKind.CLIENT:
            return
        self._inner.on_end(span)

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


def start() -> Telemetry | None:
    """Telemetry exporting over OTLP, or None when there is nowhere to send it.

    Also installs it as the global provider, so a span or metric any code
    starts by hand (`trace.get_tracer(__name__)`) carries the same resource.
    """
    if not export_configured():
        return None
    telemetry = Telemetry.sending_to(
        BatchSpanProcessor(OTLPSpanExporter()),
        PeriodicExportingMetricReader(OTLPMetricExporter()),
    )
    trace.set_tracer_provider(telemetry.tracer_provider)
    metrics.set_meter_provider(telemetry.meter_provider)
    return telemetry
