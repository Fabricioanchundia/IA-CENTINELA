"""Orders service: creates an order by calling catalog (price) and payments (charge).

It is the service users talk to, so failures in catalog or payments show up here as
503 errors. That chain of dependencies is exactly what Centinela has to untangle.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from shop import __version__
from shop.config import Settings
from shop.db import Pool, make_pool, ping
from shop.logs import configure_logging, request_id_var
from shop.metrics import ORDERS, UPSTREAM_ERRORS
from shop.web import REQUEST_ID_HEADER, install_common

SERVICE = "orders"

log = logging.getLogger("shop.orders")

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER NOT NULL,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    total_cents INTEGER NOT NULL,
    status      TEXT NOT NULL,
    payment_id  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

OrderStatus = Literal["pending", "paid", "declined", "failed"]


class OrderRequest(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=10)


class Order(BaseModel):
    id: int
    product_id: int
    quantity: int
    total_cents: int
    status: OrderStatus
    payment_id: int | None = None
    created_at: datetime


class UpstreamError(Exception):
    """A dependency (catalog or payments) did not answer correctly."""

    def __init__(self, service: str, reason: str) -> None:
        super().__init__(f"{service}: {reason}")
        self.service = service


class CatalogClient(Protocol):
    def price_cents(self, product_id: int) -> int | None: ...


class PaymentsClient(Protocol):
    def charge(self, order_ref: str, amount_cents: int) -> tuple[int, str]: ...


class OrderRepo(Protocol):
    def create(self, product_id: int, quantity: int, total_cents: int) -> Order: ...
    def update(self, order_id: int, status: OrderStatus, payment_id: int | None) -> Order: ...
    def get(self, order_id: int) -> Order | None: ...
    def ready(self) -> str: ...


def _headers() -> dict[str, str]:
    return {REQUEST_ID_HEADER: request_id_var.get()}


class HttpCatalogClient:
    def __init__(self, base_url: str, timeout_s: float) -> None:
        self.http = httpx.Client(base_url=base_url, timeout=timeout_s)

    def price_cents(self, product_id: int) -> int | None:
        try:
            resp = self.http.get(f"/products/{product_id}", headers=_headers())
        except httpx.HTTPError as exc:
            raise UpstreamError("catalog", type(exc).__name__) from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise UpstreamError("catalog", f"HTTP {resp.status_code}")
        return int(resp.json()["price_cents"])


class HttpPaymentsClient:
    def __init__(self, base_url: str, timeout_s: float) -> None:
        self.http = httpx.Client(base_url=base_url, timeout=timeout_s)

    def charge(self, order_ref: str, amount_cents: int) -> tuple[int, str]:
        try:
            resp = self.http.post(
                "/charge",
                json={"order_ref": order_ref, "amount_cents": amount_cents},
                headers=_headers(),
            )
        except httpx.HTTPError as exc:
            raise UpstreamError("payments", type(exc).__name__) from exc
        if resp.status_code != 200:
            raise UpstreamError("payments", f"HTTP {resp.status_code}")
        body = resp.json()
        return int(body["payment_id"]), str(body["status"])


class PostgresOrderRepo:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    def create(self, product_id: int, quantity: int, total_cents: int) -> Order:
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO orders (product_id, quantity, total_cents, status) "
                "VALUES (%s, %s, %s, 'pending') "
                "RETURNING id, product_id, quantity, total_cents, status, payment_id, created_at",
                (product_id, quantity, total_cents),
            ).fetchone()
        return Order.model_validate(row)

    def update(self, order_id: int, status: OrderStatus, payment_id: int | None) -> Order:
        with self.pool.connection() as conn:
            row = conn.execute(
                "UPDATE orders SET status = %s, payment_id = %s WHERE id = %s "
                "RETURNING id, product_id, quantity, total_cents, status, payment_id, created_at",
                (status, payment_id, order_id),
            ).fetchone()
        return Order.model_validate(row)

    def get(self, order_id: int) -> Order | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT id, product_id, quantity, total_cents, status, payment_id, created_at "
                "FROM orders WHERE id = %s",
                (order_id,),
            ).fetchone()
        return Order.model_validate(row) if row else None

    def ready(self) -> str:
        return ping(self.pool)


@dataclass
class InMemoryOrderRepo:
    orders: dict[int, Order] = field(default_factory=dict)

    def create(self, product_id: int, quantity: int, total_cents: int) -> Order:
        order = Order(
            id=len(self.orders) + 1,
            product_id=product_id,
            quantity=quantity,
            total_cents=total_cents,
            status="pending",
            created_at=datetime.now(UTC),
        )
        self.orders[order.id] = order
        return order

    def update(self, order_id: int, status: OrderStatus, payment_id: int | None) -> Order:
        order = self.orders[order_id].model_copy(
            update={"status": status, "payment_id": payment_id}
        )
        self.orders[order_id] = order
        return order

    def get(self, order_id: int) -> Order | None:
        return self.orders.get(order_id)

    def ready(self) -> str:
        return "ok"


@dataclass
class OrdersDeps:
    repo: OrderRepo
    catalog: CatalogClient
    payments: PaymentsClient


def place_order(deps: OrdersDeps, req: OrderRequest) -> Order:
    try:
        price = deps.catalog.price_cents(req.product_id)
    except UpstreamError as exc:
        UPSTREAM_ERRORS.labels(exc.service).inc()
        log.error("catalog unavailable", extra={"fields": {"upstream": exc.service}})
        raise HTTPException(status_code=503, detail="catalog unavailable") from exc
    if price is None:
        raise HTTPException(status_code=404, detail="product not found")

    order = deps.repo.create(req.product_id, req.quantity, price * req.quantity)
    try:
        payment_id, pay_status = deps.payments.charge(f"order-{order.id}", order.total_cents)
    except UpstreamError as exc:
        UPSTREAM_ERRORS.labels(exc.service).inc()
        ORDERS.labels("failed").inc()
        deps.repo.update(order.id, "failed", None)
        log.error(
            "payments unavailable",
            extra={"fields": {"upstream": exc.service, "order_id": order.id}},
        )
        raise HTTPException(status_code=503, detail="payments unavailable") from exc

    status: OrderStatus = "paid" if pay_status == "approved" else "declined"
    order = deps.repo.update(order.id, status, payment_id)
    ORDERS.labels(status).inc()
    log.info("order placed", extra={"fields": {"order_id": order.id, "status": status}})
    return order


def create_app(deps: OrdersDeps | None = None) -> FastAPI:
    state: dict[str, OrdersDeps] = {}
    if deps is not None:
        state["deps"] = deps

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        pool: Pool | None = None
        if "deps" not in state:
            settings = Settings.from_env(SERVICE)
            configure_logging(SERVICE)
            pool = make_pool(settings.database_url, SCHEMA)
            state["deps"] = OrdersDeps(
                repo=PostgresOrderRepo(pool),
                catalog=HttpCatalogClient(settings.catalog_url, settings.http_timeout_s),
                payments=HttpPaymentsClient(settings.payments_url, settings.http_timeout_s),
            )
            log.info("orders started", extra={"fields": {"version": __version__}})
        yield
        if pool is not None:
            pool.close()

    app = FastAPI(title="Orders service", version=__version__, lifespan=lifespan)
    install_common(app, lambda: {"database": state["deps"].repo.ready()})

    @app.post("/orders", response_model=Order, status_code=201)
    def create_order(req: OrderRequest) -> Order:
        return place_order(state["deps"], req)

    @app.get("/orders/{order_id}", response_model=Order)
    def get_order(order_id: int) -> Order:
        order = state["deps"].repo.get(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        return order

    return app


app = create_app()
