import random

import httpx
import pytest

from shop import loadgen


def test_pick_order_is_valid_and_sometimes_missing() -> None:
    rng = random.Random(42)
    picks = [loadgen.pick_order(rng) for _ in range(500)]
    assert all(1 <= p["quantity"] <= 3 for p in picks)
    missing = sum(p["product_id"] == loadgen.MISSING_PRODUCT_ID for p in picks)
    assert 20 < missing < 90  # roughly 10%


def test_run_counts_statuses_and_survives_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orders":
            raise httpx.ConnectError("orders down", request=request)
        return httpx.Response(200, json=[])

    real_client = httpx.Client
    monkeypatch.setattr(
        loadgen.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    stats = loadgen.run("http://orders", "http://catalog", rps=100, max_ticks=5)
    assert stats["200"] == 5
    assert stats["ConnectError"] == 5
