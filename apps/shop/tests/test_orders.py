from dataclasses import dataclass

from fastapi.testclient import TestClient

from shop.orders import InMemoryOrderRepo, OrdersDeps, UpstreamError, create_app


@dataclass
class FakeCatalog:
    prices: dict[int, int]
    down: bool = False

    def price_cents(self, product_id: int) -> int | None:
        if self.down:
            raise UpstreamError("catalog", "ConnectError")
        return self.prices.get(product_id)


@dataclass
class FakePayments:
    down: bool = False

    def charge(self, order_ref: str, amount_cents: int) -> tuple[int, str]:
        if self.down:
            raise UpstreamError("payments", "ReadTimeout")
        return 7, "approved" if amount_cents <= 1_000_000 else "declined"


def make(catalog_down: bool = False, payments_down: bool = False) -> tuple[TestClient, OrdersDeps]:
    deps = OrdersDeps(
        repo=InMemoryOrderRepo(),
        catalog=FakeCatalog({1: 1200, 2: 600_000}, down=catalog_down),
        payments=FakePayments(down=payments_down),
    )
    return TestClient(create_app(deps)), deps


def test_order_paid() -> None:
    client, _ = make()
    resp = client.post("/orders", json={"product_id": 1, "quantity": 2})
    assert resp.status_code == 201
    body = resp.json()
    assert body["total_cents"] == 2400
    assert body["status"] == "paid"
    assert client.get(f"/orders/{body['id']}").json()["payment_id"] == 7


def test_order_declined_when_too_expensive() -> None:
    client, _ = make()
    resp = client.post("/orders", json={"product_id": 2, "quantity": 2})
    assert resp.json()["status"] == "declined"


def test_unknown_product_404() -> None:
    client, _ = make()
    assert client.post("/orders", json={"product_id": 9, "quantity": 1}).status_code == 404


def test_catalog_down_returns_503() -> None:
    client, deps = make(catalog_down=True)
    resp = client.post("/orders", json={"product_id": 1, "quantity": 1})
    assert resp.status_code == 503
    assert isinstance(deps.repo, InMemoryOrderRepo)
    assert deps.repo.orders == {}


def test_payments_down_marks_order_failed() -> None:
    client, deps = make(payments_down=True)
    resp = client.post("/orders", json={"product_id": 1, "quantity": 1})
    assert resp.status_code == 503
    assert isinstance(deps.repo, InMemoryOrderRepo)
    assert deps.repo.orders[1].status == "failed"


def test_quantity_validation() -> None:
    client, _ = make()
    assert client.post("/orders", json={"product_id": 1, "quantity": 11}).status_code == 422
