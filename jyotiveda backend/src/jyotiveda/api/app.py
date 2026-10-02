"""FastAPI application factory. One image, many roles (JYOTIVEDA_ROLE selects routers/workers)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from contextlib import asynccontextmanager

import orjson
import structlog
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from jyotiveda import __version__
from jyotiveda.api import routes as r
from jyotiveda.config import ServiceRole, Settings, get_settings
from jyotiveda.copilot.agent import Copilot
from jyotiveda.events import CloudEvent
from jyotiveda.observability import configure_logging, configure_tracing
from jyotiveda.runtime.platform import Platform
from jyotiveda.security.rbac import decode_token

log = structlog.get_logger(__name__)
HTTP_REQS = Counter("jyotiveda_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LAT = Histogram("jyotiveda_http_request_seconds", "HTTP latency", ["method", "route"])

ROLE_ROUTERS = {
    ServiceRole.GATEWAY: [r.auth, r.transformers, r.analytics, r.events],
    ServiceRole.TWIN: [r.transformers, r.simulation],
    ServiceRole.FORECAST: [r.forecast],
    ServiceRole.GRID_INTEL: [r.reliability],
    ServiceRole.RELIABILITY: [r.reliability, r.fairness],
    ServiceRole.FLEXIBILITY: [r.flexibility, r.fairness],
    ServiceRole.OPTIMIZATION: [r.reliability],
    ServiceRole.DISPATCH: [r.dispatch, r.edge, r.audit],
    ServiceRole.COPILOT: [r.copilot],
    ServiceRole.CONTROL_LOOP: [],
}
ALL = [
    r.auth,
    r.transformers,
    r.forecast,
    r.reliability,
    r.flexibility,
    r.fairness,
    r.simulation,
    r.dispatch,
    r.edge,
    r.audit,
    r.copilot,
    r.analytics,
    r.events,
]


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    configure_logging(s)
    if s.role == ServiceRole.CONTROL_LOOP:
        s = s.model_copy(update={"control_loop_enabled": True})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        platform = Platform(s)
        app.state.platform = platform
        app.state.copilot = Copilot(platform)
        await platform.start()
        log.info(
            "jyotiveda_started",
            role=s.role.value,
            env=s.env.value,
            version=__version__,
            transformers=len(platform.fleet),
        )
        yield
        await platform.stop()

    app = FastAPI(
        title="Jyotiveda API",
        version=__version__,
        description="Transformer-scale renewable reliability operating system — digital twin, probabilistic "
        "forecasting, Reliability Budget, fairness-aware flexibility market, CVaR-MPC, safety-shielded dispatch.",
        lifespan=lifespan,
        openapi_tags=[
            {"name": t}
            for t in (
                "twin",
                "forecast",
                "reliability",
                "flexibility",
                "fairness",
                "digital-twin",
                "dispatch",
                "edge",
                "audit",
                "copilot",
                "analytics",
                "events",
                "auth",
            )
        ],
    )
    configure_tracing(app, s)
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=rid)
        t0 = time.perf_counter()
        try:
            resp = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        route = getattr(request.scope.get("route"), "path", request.url.path)
        HTTP_REQS.labels(request.method, route, resp.status_code).inc()
        HTTP_LAT.labels(request.method, route).observe(time.perf_counter() - t0)
        resp.headers.update(
            {
                "x-request-id": rid,
                "x-content-type-options": "nosniff",
                "x-frame-options": "DENY",
                "referrer-policy": "no-referrer",
                "strict-transport-security": "max-age=63072000; includeSubDomains",
            }
        )
        return resp

    @app.exception_handler(Exception)
    async def problem(request: Request, exc: Exception):  # RFC 9457 problem+json
        log.exception("unhandled_error", path=request.url.path)
        return JSONResponse(
            {
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": str(exc) if not s.is_prod else "internal error",
            },
            status_code=500,
            media_type="application/problem+json",
        )

    for router in ALL if s.role in (ServiceRole.ALL,) else ROLE_ROUTERS.get(s.role, []):
        app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> dict:
        p: Platform = request.app.state.platform
        return {
            "status": "ready",
            "role": s.role.value,
            "transformers": len(p.fleet),
            "bus": s.event_bus,
            "audit_head": p.ledger.head[:16],
        }

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/v1/version", tags=["analytics"])
    async def version() -> dict:
        return {
            "name": "jyotiveda",
            "version": __version__,
            "role": s.role.value,
            "llm_model": s.llm_model,
            "forecast_backend": s.forecast_backend,
            "mpc_solver": s.mpc_solver,
        }

    @app.websocket("/ws/live")
    async def live(ws: WebSocket, token: str, topics: str = "*", transformer_id: str | None = None):
        """Live event stream for the command centre: CloudEvents as JSON, filtered by topic/transformer.
        Auth: ?token=<JWT> (browsers cannot set headers on WebSocket upgrades)."""
        try:
            principal = decode_token(token, s)
        except Exception:
            # Accept first so the browser receives close code 4401 (a pre-accept close surfaces only as
            # HTTP 403 / code 1006, and the client cannot tell "bad token" from "server down").
            await ws.accept()
            await ws.close(code=4401, reason="invalid or expired token")
            return
        await ws.accept()
        p: Platform = ws.app.state.platform
        wanted = None if topics == "*" else set(topics.split(","))
        queue: asyncio.Queue[CloudEvent] = asyncio.Queue(maxsize=1000)

        async def handler(e: CloudEvent) -> None:
            if wanted and str(e.type) not in wanted:
                return
            if transformer_id and e.subject not in (None, transformer_id):
                return
            if e.subject and not principal.in_scope(e.subject):
                return
            with contextlib.suppress(asyncio.QueueFull):  # slow consumer: drop, never block producers
                queue.put_nowait(e)

        p.bus.subscribe("*", handler)
        await ws.send_text(
            orjson.dumps({"type": "hello", "principal": principal.sub, "role": principal.role.value}).decode()
        )
        for e in p.bus.recent(50):
            await handler(e)
        try:
            while True:
                e = await queue.get()
                await ws.send_text(orjson.dumps(e.model_dump(mode="json")).decode())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            subs = getattr(p.bus, "_subs", {})
            if "*" in subs and handler in subs["*"]:
                subs["*"].remove(handler)

    return app


app = None  # uvicorn factory: `uvicorn jyotiveda.api.app:create_app --factory`
