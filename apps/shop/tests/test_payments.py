from fastapi.testclient import TestClient

from shop.payments import MAX_APPROVED_CENTS, InMemoryPaymentRepo, create_app, decide


def test_decide() -> None:
    assert decide(MAX_APPROVED_CENTS) == "approved"
    assert decide(MAX_APPROVED_CENTS + 1) == "declined"


def test_charge_is_saved() -> None:
    repo = InMemoryPaymentRepo()
    client = TestClient(create_app(repo))
    resp = client.post("/charge", json={"order_ref": "order-1", "amount_cents": 2500})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert repo.rows == [("order-1", 2500, "approved")]


def test_charge_rejects_invalid_amount() -> None:
    client = TestClient(create_app(InMemoryPaymentRepo()))
    resp = client.post("/charge", json={"order_ref": "order-1", "amount_cents": 0})
    assert resp.status_code == 422
