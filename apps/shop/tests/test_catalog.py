import pytest
from fastapi.testclient import TestClient

from shop.catalog import (
    CACHE_KEY,
    CatalogDeps,
    InMemoryCache,
    InMemoryProductRepo,
    Product,
    create_app,
)


@pytest.fixture
def deps() -> CatalogDeps:
    repo = InMemoryProductRepo(
        {1: Product(id=1, sku="MUG-01", name="Mug", price_cents=1200, stock=5)}
    )
    return CatalogDeps(repo=repo, cache=InMemoryCache())


def test_list_products_fills_cache(deps: CatalogDeps) -> None:
    client = TestClient(create_app(deps))
    resp = client.get("/products")
    assert resp.status_code == 200
    assert resp.json()[0]["sku"] == "MUG-01"
    assert isinstance(deps.cache, InMemoryCache)
    assert CACHE_KEY in deps.cache.data


def test_list_products_works_when_cache_is_down(deps: CatalogDeps) -> None:
    assert isinstance(deps.cache, InMemoryCache)
    deps.cache.broken = True
    client = TestClient(create_app(deps))
    resp = client.get("/products")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_product_and_404(deps: CatalogDeps) -> None:
    client = TestClient(create_app(deps))
    assert client.get("/products/1").json()["price_cents"] == 1200
    assert client.get("/products/99").status_code == 404


def test_health_and_request_id(deps: CatalogDeps) -> None:
    client = TestClient(create_app(deps))
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz").json()
    assert ready == {"status": "ok", "database": "ok", "cache": "ok"}
    resp = client.get("/products", headers={"X-Request-ID": "abc123"})
    assert resp.headers["X-Request-ID"] == "abc123"
