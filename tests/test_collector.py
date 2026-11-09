from datetime import UTC, datetime, timedelta

import httpx

from centinela.incidents.collector import Collector
from centinela.incidents.models import Alert
from centinela.incidents.sources import LokiLogs, PrometheusAlerts, logql_for
from centinela.incidents.store import InMemoryIncidentStore
from tests.test_tracker import ERRORS, SERVICE_DOWN, T0


class FakeAlerts:
    def __init__(self) -> None:
        self.now: list[Alert] = []

    def firing(self) -> list[Alert]:
        return list(self.now)


class FakeLogs:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str | None] = []

    def recent(
        self, service: str | None, start: datetime, end: datetime, limit: int = 20
    ) -> list[dict[str, object]]:
        self.calls.append(service)
        if self.fail:
            raise httpx.ConnectError("loki down")
        return [{"msg": "payments unavailable", "level": "error"}]


class Clock:
    def __init__(self) -> None:
        self.t = T0 + timedelta(minutes=1)

    def __call__(self) -> datetime:
        return self.t


def test_full_incident_lifecycle_with_mttd_and_mttr() -> None:
    alerts, store, clock = FakeAlerts(), InMemoryIncidentStore(), Clock()
    collector = Collector(alerts, store, FakeLogs(), clock)

    alerts.now = [SERVICE_DOWN]  # payments goes down at T0, detected at T0+1m
    collector.cycle()
    clock.t += timedelta(minutes=1)
    alerts.now = [SERVICE_DOWN, ERRORS]  # the symptom on orders joins the same incident
    collector.cycle()
    clock.t += timedelta(minutes=3)
    alerts.now = []  # recovery
    collector.cycle()

    [incident] = store.recent()
    assert incident.status == "resolved"
    assert incident.category == "ServiceDown"
    assert incident.service == "payments"
    assert incident.mttd_seconds == 60
    assert incident.mttr_seconds == 4 * 60
    kinds = [e["kind"] for e in store.events(incident.id)]
    assert kinds.count("alert_firing") == 2
    assert kinds.count("logs") == 2
    assert kinds[-1] == "incident_resolved"


def test_loki_failure_does_not_lose_the_incident() -> None:
    alerts, store = FakeAlerts(), InMemoryIncidentStore()
    alerts.now = [SERVICE_DOWN]
    Collector(alerts, store, FakeLogs(fail=True), Clock()).cycle()
    [incident] = store.recent()
    assert incident.status == "open"
    assert "logs" not in [e["kind"] for e in store.events(incident.id)]


def test_postmortem_can_be_recorded() -> None:
    alerts, store = FakeAlerts(), InMemoryIncidentStore()
    alerts.now = [SERVICE_DOWN]
    Collector(alerts, store, None, Clock()).cycle()
    assert store.close(1, "payments scaled to 0", "scaled back to 1 replica")
    incident = store.get(1)
    assert incident is not None
    assert incident.root_cause == "payments scaled to 0"
    assert not store.close(99, "x", "y")


def test_prometheus_source_keeps_only_firing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/alerts"
        base = {"annotations": {}, "activeAt": "2026-11-09T12:00:00Z"}
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "alerts": [
                        {**base, "state": "firing", "labels": {"alertname": "A"}},
                        {**base, "state": "pending", "labels": {"alertname": "B"}},
                    ]
                },
            },
        )

    client = httpx.Client(base_url="http://prom", transport=httpx.MockTransport(handler))
    assert [a.name for a in PrometheusAlerts("http://prom", client).firing()] == ["A"]


def test_loki_source_parses_json_lines() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert 'service="orders"' in request.url.params["query"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "result": [
                        {
                            "stream": {"service": "orders"},
                            "values": [
                                ["1", '{"ts": "2026-11-09T12:00:01", "msg": "payments down"}'],
                                ["2", "plain text line"],
                            ],
                        }
                    ]
                }
            },
        )

    client = httpx.Client(base_url="http://loki", transport=httpx.MockTransport(handler))
    lines = LokiLogs("http://loki", client).recent("orders", T0, T0 + timedelta(minutes=1))
    assert lines[0]["msg"] == "payments down"
    assert {line["service"] for line in lines} == {"orders"}


def test_logql_selector() -> None:
    assert logql_for(None) == '{project="centinela", level=~"error|warning"}'
    assert 'service="payments"' in logql_for("payments")


def test_clock_default_is_timezone_aware() -> None:
    collector = Collector(FakeAlerts(), InMemoryIncidentStore())
    assert collector.clock().tzinfo is UTC
