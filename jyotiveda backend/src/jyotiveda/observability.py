"""Structured logging (structlog JSON), Prometheus metrics and OpenTelemetry tracing."""

from __future__ import annotations

import logging
import sys

import structlog
from prometheus_client import Counter, Gauge, Histogram

from jyotiveda.config import Settings

# ---- domain metrics (scraped by Prometheus, alerted by Alertmanager) --------------------------
CONTROL_CYCLE_SECONDS = Histogram(
    "jyotiveda_control_cycle_seconds", "End-to-end Sense->Dispatch cycle latency", ["transformer"]
)
FORECAST_LATENCY = Histogram("jyotiveda_forecast_seconds", "Forecast inference latency", ["backend"])
MPC_SOLVE_SECONDS = Histogram("jyotiveda_mpc_solve_seconds", "MPC solve time", ["status"])
SHIELD_VERDICTS = Counter("jyotiveda_shield_verdicts_total", "Safety shield verdicts", ["verdict"])
SHIELD_VIOLATIONS = Counter("jyotiveda_shield_violations_total", "Constraint violations caught", ["rule"])
DISPATCH_TRANSITIONS = Counter("jyotiveda_dispatch_transitions_total", "Dispatch state changes", ["to_state"])
RELIABILITY_GAP_KWH = Gauge("jyotiveda_reliability_gap_kwh", "Forecast reliability gap", ["transformer"])
CRITICAL_PROTECTED = Gauge(
    "jyotiveda_critical_protected_ratio", "Share of critical load served", ["transformer"]
)
FALLBACK_ACTIVATIONS = Counter(
    "jyotiveda_fallback_activations_total", "Degraded-mode fallbacks", ["component"]
)


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    renderer = structlog.processors.JSONRenderer() if settings.log_json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def configure_tracing(app, settings: Settings) -> None:  # pragma: no cover - needs a collector
    if not settings.otlp_endpoint:
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": f"jyotiveda-{settings.role}"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
