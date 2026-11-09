"""Runs against a real PostgreSQL when PG* variables are set (CI service container)."""

import os
import uuid
from datetime import timedelta

import pytest

from centinela.incidents.store import PostgresIncidentStore, make_pool
from tests.test_tracker import ERRORS, SERVICE_DOWN, T0

pytestmark = pytest.mark.skipif(not os.environ.get("PGHOST"), reason="needs PostgreSQL")


@pytest.fixture
def store() -> PostgresIncidentStore:
    s = PostgresIncidentStore(make_pool())
    s.migrate(reader_password=uuid.uuid4().hex)
    with s.pool.connection() as conn:
        conn.execute("TRUNCATE incidents RESTART IDENTITY CASCADE")
    return s


def test_lifecycle_and_metrics_view(store: PostgresIncidentStore) -> None:
    detected = T0 + timedelta(minutes=1)
    incident_id = store.create((SERVICE_DOWN,), detected)
    store.attach(incident_id, ERRORS, detected + timedelta(minutes=1))
    [open_] = store.open_incidents()
    assert open_.active_alerts == {SERVICE_DOWN.fingerprint, ERRORS.fingerprint}

    store.resolve_alert(incident_id, SERVICE_DOWN.fingerprint, detected + timedelta(minutes=3))
    store.resolve_alert(incident_id, ERRORS.fingerprint, detected + timedelta(minutes=4))
    store.resolve_incident(incident_id, detected + timedelta(minutes=4))
    assert store.open_incidents() == []

    incident = store.get(incident_id)
    assert incident is not None
    assert (incident.mttd_seconds, incident.mttr_seconds) == (60, 240)
    with store.pool.connection() as conn:
        row = conn.execute("SELECT mttd_seconds, mttr_seconds FROM incident_metrics").fetchone()
    assert row is not None
    assert (float(row["mttd_seconds"]), float(row["mttr_seconds"])) == (60.0, 240.0)
    kinds = [e["kind"] for e in store.events(incident_id)]
    assert kinds == [
        "alert_firing",
        "alert_firing",
        "alert_resolved",
        "alert_resolved",
        "incident_resolved",
    ]


def test_reader_role_can_only_read(store: PostgresIncidentStore) -> None:
    with store.pool.connection() as conn:
        grants = conn.execute(
            "SELECT DISTINCT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'grafana_reader'"
        ).fetchall()
    assert {g["privilege_type"] for g in grants} == {"SELECT"}


def test_migrate_is_idempotent(store: PostgresIncidentStore) -> None:
    store.migrate(reader_password=uuid.uuid4().hex)  # rotate
    store.migrate(reader_password=None)


def test_alerts_and_similar_incidents(store: PostgresIncidentStore) -> None:
    first = store.create((SERVICE_DOWN, ERRORS), T0)
    store.resolve_alert(first, SERVICE_DOWN.fingerprint, T0 + timedelta(minutes=3))
    alerts = store.alerts(first)
    assert [a.name for a in alerts] == ["ServiceDown", "HighErrorRate"]
    assert alerts[0].resolved_at == T0 + timedelta(minutes=3) and alerts[1].resolved_at is None
    assert alerts[0].service == "payments"

    second = store.create((SERVICE_DOWN,), T0 + timedelta(hours=1))
    incident = store.get(second)
    assert incident is not None
    assert store.similar(incident) == []  # no postmortem written yet
    store.close(first, "payments scaled to 0", "scaled back")
    [past] = store.similar(incident)
    assert past.id == first and past.root_cause == "payments scaled to 0"
