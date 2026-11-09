from datetime import UTC, datetime, timedelta

from centinela.incidents.models import Alert, OpenIncident, fingerprint
from centinela.incidents.tracker import (
    AttachAlert,
    CreateIncident,
    ResolveAlert,
    ResolveIncident,
    plan,
)

T0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def alert(name: str, severity: str, at: datetime, **labels: str) -> Alert:
    full = {"alertname": name, "severity": severity, **labels}
    return Alert(fingerprint(full), name, severity, full, f"{name} summary", at)


SERVICE_DOWN = alert("ServiceDown", "critical", T0, service="payments")
ERRORS = alert("HighErrorRate", "critical", T0 + timedelta(seconds=30), service="orders")
UPSTREAM = alert("UpstreamErrors", "warning", T0 + timedelta(seconds=10), upstream="payments")


def test_nothing_firing_nothing_open() -> None:
    assert plan([], [], T0) == []


def test_first_alerts_open_one_incident_with_primary_first() -> None:
    actions = plan([UPSTREAM, ERRORS, SERVICE_DOWN], [], T0)
    assert len(actions) == 1
    create = actions[0]
    assert isinstance(create, CreateIncident)
    # most severe first, then earliest: ServiceDown (critical, T0) leads
    assert [a.name for a in create.alerts] == ["ServiceDown", "HighErrorRate", "UpstreamErrors"]


def test_cascading_alert_joins_the_open_incident() -> None:
    open_ = [OpenIncident(1, "critical", T0, {SERVICE_DOWN.fingerprint})]
    actions = plan([SERVICE_DOWN, ERRORS], open_, T0 + timedelta(minutes=2))
    assert actions == [AttachAlert(1, ERRORS)]


def test_already_known_alerts_do_nothing() -> None:
    open_ = [OpenIncident(1, "critical", T0, {SERVICE_DOWN.fingerprint})]
    assert plan([SERVICE_DOWN], open_, T0) == []


def test_partial_recovery_resolves_only_that_alert() -> None:
    open_ = [OpenIncident(1, "critical", T0, {SERVICE_DOWN.fingerprint, ERRORS.fingerprint})]
    actions = plan([ERRORS], open_, T0 + timedelta(minutes=5))
    assert actions == [ResolveAlert(1, SERVICE_DOWN.fingerprint)]


def test_full_recovery_resolves_the_incident() -> None:
    open_ = [OpenIncident(1, "critical", T0, {SERVICE_DOWN.fingerprint, ERRORS.fingerprint})]
    actions = plan([], open_, T0 + timedelta(minutes=6))
    assert ResolveIncident(1) in actions
    assert sum(isinstance(a, ResolveAlert) for a in actions) == 2


def test_new_alert_while_old_one_clears_keeps_incident_open() -> None:
    open_ = [OpenIncident(1, "critical", T0, {SERVICE_DOWN.fingerprint})]
    actions = plan([ERRORS], open_, T0 + timedelta(minutes=3))
    assert AttachAlert(1, ERRORS) in actions
    assert ResolveAlert(1, SERVICE_DOWN.fingerprint) in actions
    assert ResolveIncident(1) not in actions


def test_alert_from_prometheus_payload() -> None:
    raw = {
        "labels": {"alertname": "ServiceDown", "severity": "critical", "service": "payments"},
        "annotations": {"summary": "payments is down"},
        "state": "firing",
        "activeAt": "2026-11-09T16:06:36.076688437Z",
    }
    a = Alert.from_prometheus(raw)
    assert a.name == "ServiceDown"
    assert a.service == "payments"
    assert a.summary == "payments is down"
    assert a.active_at.tzinfo is not None
    assert a.fingerprint == Alert.from_prometheus(raw).fingerprint
