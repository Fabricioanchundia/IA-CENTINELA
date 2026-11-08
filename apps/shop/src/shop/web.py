"""Pieces shared by the three services: request IDs, access logs and health endpoints."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from shop.logs import request_id_var

log = logging.getLogger("shop.http")

REQUEST_ID_HEADER = "X-Request-ID"


def install_common(app: FastAPI, readiness: Callable[[], dict[str, str]]) -> None:
    """Add request-id + access-log middleware and /healthz, /readyz endpoints."""

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            if request.url.path not in ("/healthz", "/readyz"):
                fields: dict[str, Any] = {
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
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
