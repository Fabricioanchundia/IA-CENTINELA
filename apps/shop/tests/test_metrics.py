from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from shop.catalog import CatalogDeps, InMemoryCache, InMemoryProductRepo, Product
from shop.catalog import create_app as catalog_app


def sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def make_client(cache_broken: bool = False) -> TestClient:
    repo = InMemoryProductRepo({1: Product(id=1, sku="A", name="A", price_cents=100, stock=1)})
    return TestClient(catalog_app(CatalogDeps(repo=repo, cache=InMemoryCache(broken=cache_broken))))


def test_metrics_endpoint_exposes_prometheus_text() -> None:
    client = make_client()
    client.get("/products")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in resp.text
    assert "process_resident_memory_bytes" in resp.text


def test_route_template_keeps_cardinality_low() -> None:
    client = make_client()
    labels = {"method": "GET", "route": "/products/{product_id}", "status": "200"}
    before = sample("http_requests_total", labels)
    client.get("/products/1")
    assert sample("http_requests_total", labels) == before + 1
    # the raw path must never become a label value
    assert sample("http_requests_total", {**labels, "route": "/products/1"}) == 0


def test_health_and_metrics_are_not_counted() -> None:
    client = make_client()
    labels = {"method": "GET", "route": "/healthz", "status": "200"}
    client.get("/healthz")
    client.get("/metrics")
    assert sample("http_requests_total", labels) == 0


def test_cache_results_are_counted() -> None:
    hit_before = sample("shop_cache_requests_total", {"result": "hit"})
    miss_before = sample("shop_cache_requests_total", {"result": "miss"})
    client = make_client()
    client.get("/products")  # miss, then stored
    client.get("/products")  # hit
    assert sample("shop_cache_requests_total", {"result": "miss"}) == miss_before + 1
    assert sample("shop_cache_requests_total", {"result": "hit"}) == hit_before + 1

    error_before = sample("shop_cache_requests_total", {"result": "error"})
    make_client(cache_broken=True).get("/products")
    assert sample("shop_cache_requests_total", {"result": "error"}) == error_before + 1
