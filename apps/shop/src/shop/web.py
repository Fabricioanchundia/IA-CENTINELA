"""Pieces shared by the three services: request IDs, access logs, metrics and health."""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from shop.chaos import Chaos
from shop.logs import request_id_var
from shop.metrics import HTTP_LATENCY, HTTP_REQUESTS

log = logging.getLogger("shop.http")

REQUEST_ID_HEADER = "X-Request-ID"
UNINSTRUMENTED = ("/healthz", "/readyz", "/metrics")


def route_template(request: Request) -> str:
    """`/orders/{order_id}` instead of `/orders/42`, so metrics keep few label values."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def install_common(app: FastAPI, readiness: Callable[[], dict[str, str]]) -> None:
    """Add request-id, access-log and metrics middleware plus /healthz, /readyz, /metrics."""
    chaos = Chaos.from_env()  # Phase 9 fault injection, off unless configured
    app.state.chaos = chaos

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status = 500
        try:
            if chaos.active and request.url.path not in UNINSTRUMENTED:
                chaos.leak()
                if chaos.latency_s:
                    await asyncio.sleep(chaos.latency_s)
                if chaos.should_fail():
                    log.error("unhandled error while processing request")
                    failed = JSONResponse({"detail": "internal server error"}, status_code=500)
                    failed.headers[REQUEST_ID_HEADER] = request_id
                    return failed
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            if request.url.path not in UNINSTRUMENTED:
                elapsed = time.perf_counter() - start
                route = route_template(request)
                HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
                HTTP_LATENCY.labels(request.method, route).observe(elapsed)
                fields: dict[str, Any] = {
                    "method": request.method,
                    "path": request.url.path,
                    "route": route,
                    "status": status,
                    "duration_ms": round(elapsed * 1000, 1),
                }
                level = logging.ERROR if status >= 500 else logging.INFO
                log.log(level, "request", extra={"fields": fields})
            request_id_var.reset(token)

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        """Liveness: the process is up."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    def readyz() -> JSONResponse:
        """Readiness: dependencies (database, cache) are reachable."""
        checks = readiness()
        ok = checks.get("database") == "ok"
        return JSONResponse({"status": "ok" if ok else "fail", **checks}, 200 if ok else 503)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        """Prometheus scrape endpoint."""
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
