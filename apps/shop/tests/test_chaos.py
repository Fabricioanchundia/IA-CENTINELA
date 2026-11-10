"""Phase 9 fault injection: off by default, only via environment, never on health endpoints."""

import time

import pytest
from fastapi.testclient import TestClient

from shop.chaos import Chaos
from shop.payments import InMemoryPaymentRepo, create_app

CHARGE = {"order_ref": "order-1", "amount_cents": 2500}


def test_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CHAOS_LATENCY_S", "CHAOS_ERROR_RATIO", "CHAOS_LEAK_KB"):
        monkeypatch.delenv(name, raising=False)
    assert not Chaos.from_env().active
    client = TestClient(create_app(InMemoryPaymentRepo()))
    assert all(client.post("/charge", json=CHARGE).status_code == 200 for _ in range(20))


def test_errors_hit_business_endpoints_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAOS_ERROR_RATIO", "1")
    repo = InMemoryPaymentRepo()
    client = TestClient(create_app(repo))
    resp = client.post("/charge", json=CHARGE)
    assert resp.status_code == 500 and "chaos" not in resp.text.lower()
    assert repo.rows == []  # the request never reached the handler
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
    assert 'status="500"' in client.get("/metrics").text  # monitoring sees it


def test_leak_grows_and_values_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAOS_LEAK_KB", "999999")
    monkeypatch.setenv("CHAOS_ERROR_RATIO", "7")
    monkeypatch.setenv("CHAOS_LATENCY_S", "-3")
    chaos = Chaos.from_env()
    assert (chaos.leak_kb, chaos.error_ratio, chaos.latency_s) == (1024, 1.0, 0.0)
    chaos = Chaos(leak_kb=4)
    chaos.leak()
    chaos.leak()
    assert sum(len(b) for b in chaos.leaked) == 8 * 1024


def test_latency_is_added(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAOS_LATENCY_S", "0.2")
    client = TestClient(create_app(InMemoryPaymentRepo()))
    started = time.perf_counter()
    assert client.post("/charge", json=CHARGE).status_code == 200
    assert time.perf_counter() - started >= 0.2
    started = time.perf_counter()
    assert client.get("/healthz").status_code == 200
    assert time.perf_counter() - started < 0.2  # probes are never slowed down
