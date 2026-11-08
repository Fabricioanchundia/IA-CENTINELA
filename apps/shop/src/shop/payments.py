"""Payments service: a fake payment gateway.

Charges up to US$10,000.00 are approved; bigger ones are declined. No real money,
no card data: it only exists so that orders depend on a third service.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal, Protocol

from fastapi import FastAPI
from pydantic import BaseModel, Field

from shop import __version__
from shop.config import Settings
from shop.db import Pool, make_pool, ping
from shop.logs import configure_logging
from shop.web import install_common

SERVICE = "payments"
MAX_APPROVED_CENTS = 1_000_000

log = logging.getLogger("shop.payments")

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id           SERIAL PRIMARY KEY,
    order_ref    TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    status       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

Status = Literal["approved", "declined"]


class ChargeRequest(BaseModel):
    order_ref: str = Field(min_length=1, max_length=64)
    amount_cents: int = Field(gt=0)


class Charge(BaseModel):
    payment_id: int
    order_ref: str
    amount_cents: int
    status: Status


class PaymentRepo(Protocol):
    def save(self, order_ref: str, amount_cents: int, status: Status) -> int: ...
    def ready(self) -> str: ...


class PostgresPaymentRepo:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    def save(self, order_ref: str, amount_cents: int, status: Status) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO payments (order_ref, amount_cents, status) "
                "VALUES (%s, %s, %s) RETURNING id",
                (order_ref, amount_cents, status),
            ).fetchone()
        assert row is not None  # noqa: S101 - RETURNING always yields a row
        return int(row["id"])

    def ready(self) -> str:
        return ping(self.pool)


@dataclass
class InMemoryPaymentRepo:
    rows: list[tuple[str, int, Status]] = field(default_factory=list)

    def save(self, order_ref: str, amount_cents: int, status: Status) -> int:
        self.rows.append((order_ref, amount_cents, status))
        return len(self.rows)

    def ready(self) -> str:
        return "ok"


def decide(amount_cents: int) -> Status:
    return "approved" if amount_cents <= MAX_APPROVED_CENTS else "declined"


def create_app(repo: PaymentRepo | None = None) -> FastAPI:
    state: dict[str, PaymentRepo] = {}
    if repo is not None:
        state["repo"] = repo

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        pool: Pool | None = None
        if "repo" not in state:
            settings = Settings.from_env(SERVICE)
            configure_logging(SERVICE)
            pool = make_pool(settings.database_url, SCHEMA)
            state["repo"] = PostgresPaymentRepo(pool)
            log.info("payments started", extra={"fields": {"version": __version__}})
        yield
        if pool is not None:
            pool.close()

    app = FastAPI(title="Payments service", version=__version__, lifespan=lifespan)
    install_common(app, lambda: {"database": state["repo"].ready()})

    @app.post("/charge", response_model=Charge)
    def charge(req: ChargeRequest) -> Charge:
        status = decide(req.amount_cents)
        payment_id = state["repo"].save(req.order_ref, req.amount_cents, status)
        log.info(
            "charge processed",
            extra={"fields": {"order_ref": req.order_ref, "status": status}},
        )
        return Charge(
            payment_id=payment_id,
            order_ref=req.order_ref,
            amount_cents=req.amount_cents,
            status=status,
        )

    return app


app = create_app()
