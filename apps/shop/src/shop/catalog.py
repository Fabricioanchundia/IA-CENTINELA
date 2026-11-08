"""Catalog service: product list and prices.

PostgreSQL is the source of truth; Redis caches the product list. If Redis is down the
service keeps working from the database (graceful degradation) and logs a warning.
"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol

import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shop import __version__
from shop.config import Settings
from shop.db import Pool, make_pool, ping
from shop.logs import configure_logging
from shop.web import install_common

SERVICE = "catalog"
CACHE_KEY = "catalog:products"
CACHE_TTL_S = 30

log = logging.getLogger("shop.catalog")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          SERIAL PRIMARY KEY,
    sku         TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents > 0),
    stock       INTEGER NOT NULL DEFAULT 0
);
INSERT INTO products (sku, name, price_cents, stock) VALUES
    ('MUG-01', 'Centinela mug', 1200, 50),
    ('TSH-01', 'SRE t-shirt', 2500, 30),
    ('STK-01', 'Sticker pack', 500, 200),
    ('HOD-01', 'On-call hoodie', 4900, 15),
    ('BOT-01', 'Water bottle', 1800, 40)
ON CONFLICT (sku) DO NOTHING;
"""


class Product(BaseModel):
    id: int
    sku: str
    name: str
    price_cents: int
    stock: int


class ProductRepo(Protocol):
    def list(self) -> list[Product]: ...
    def get(self, product_id: int) -> Product | None: ...
    def ready(self) -> str: ...


class Cache(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl_s: int) -> None: ...
    def ready(self) -> str: ...


class PostgresProductRepo:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    def list(self) -> list[Product]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, sku, name, price_cents, stock FROM products ORDER BY id"
            ).fetchall()
        return [Product.model_validate(r) for r in rows]

    def get(self, product_id: int) -> Product | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT id, sku, name, price_cents, stock FROM products WHERE id = %s",
                (product_id,),
            ).fetchone()
        return Product.model_validate(row) if row else None

    def ready(self) -> str:
        return ping(self.pool)


class RedisCache:
    def __init__(self, url: str) -> None:
        self.client = redis.Redis.from_url(
            url, socket_timeout=0.5, socket_connect_timeout=0.5, decode_responses=True
        )

    def get(self, key: str) -> str | None:
        value = self.client.get(key)
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str, ttl_s: int) -> None:
        self.client.set(key, value, ex=ttl_s)

    def ready(self) -> str:
        try:
            self.client.ping()
            return "ok"
        except redis.RedisError:
            return "fail"


@dataclass
class InMemoryProductRepo:
    products: dict[int, Product] = field(default_factory=dict)

    def list(self) -> list[Product]:
        return [self.products[k] for k in sorted(self.products)]

    def get(self, product_id: int) -> Product | None:
        return self.products.get(product_id)

    def ready(self) -> str:
        return "ok"


@dataclass
class InMemoryCache:
    data: dict[str, str] = field(default_factory=dict)
    broken: bool = False

    def get(self, key: str) -> str | None:
        if self.broken:
            raise redis.ConnectionError("cache down")
        return self.data.get(key)

    def set(self, key: str, value: str, ttl_s: int) -> None:
        if self.broken:
            raise redis.ConnectionError("cache down")
        self.data[key] = value

    def ready(self) -> str:
        return "fail" if self.broken else "ok"


@dataclass
class CatalogDeps:
    repo: ProductRepo
    cache: Cache


def list_products(deps: CatalogDeps) -> list[Product]:
    try:
        cached = deps.cache.get(CACHE_KEY)
        if cached is not None:
            return [Product.model_validate(p) for p in json.loads(cached)]
    except redis.RedisError:
        log.warning("cache unavailable, reading from database")
    products = deps.repo.list()
    try:
        deps.cache.set(CACHE_KEY, json.dumps([p.model_dump() for p in products]), CACHE_TTL_S)
    except redis.RedisError:
        log.warning("cache unavailable, could not store product list")
    return products


def create_app(deps: CatalogDeps | None = None) -> FastAPI:
    state: dict[str, CatalogDeps] = {}
    if deps is not None:
        state["deps"] = deps

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        pool: Pool | None = None
        if "deps" not in state:
            settings = Settings.from_env(SERVICE)
            configure_logging(SERVICE)
            pool = make_pool(settings.database_url, SCHEMA)
            state["deps"] = CatalogDeps(PostgresProductRepo(pool), RedisCache(settings.redis_url))
            log.info("catalog started", extra={"fields": {"version": __version__}})
        yield
        if pool is not None:
            pool.close()

    app = FastAPI(title="Catalog service", version=__version__, lifespan=lifespan)

    def readiness() -> dict[str, str]:
        d = state["deps"]
        return {"database": d.repo.ready(), "cache": d.cache.ready()}

    install_common(app, readiness)

    @app.get("/products", response_model=list[Product])
    def get_products() -> list[Product]:
        return list_products(state["deps"])

    @app.get("/products/{product_id}", response_model=Product)
    def get_product(product_id: int) -> Product:
        product = state["deps"].repo.get(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="product not found")
        return product

    return app


app = create_app()
