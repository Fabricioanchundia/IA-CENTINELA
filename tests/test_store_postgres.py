"""Runs against a real PostgreSQL when PG* variables are set (CI service container)."""

import os
import uuid
from datetime import UTC, datetime, timedelta

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


def test_action_lifecycle_and_audit(store: PostgresIncidentStore) -> None:
    from centinela.actions.approvals import ApprovalError, approve
    from centinela.actions.policy import PENDING_TTL, check
    from centinela.actions.store import PostgresActionStore

    actions = PostgresActionStore(store.pool)
    incident_id = store.create((SERVICE_DOWN,), T0)
    action = actions.propose(
        incident_id, check("scale_up", "payments"), "down", "centinela-agent", T0
    )
    assert action is not None and action.status == "pending" and action.params == {"replicas": 1}
    assert actions.propose(incident_id, check("restart", "orders"), "x", "agent", T0) is None

    approved = approve(actions, action.id, "Fabricio", T0 + timedelta(minutes=1))
    assert approved.status == "approved"
    with pytest.raises(ApprovalError, match="already approved"):
        approve(actions, action.id, "Ana", T0 + timedelta(minutes=2))
    assert [a.id for a in actions.approved()] == [action.id]

    done = actions.finish(action.id, "executed", "0/0 -> 1/1", T0 + timedelta(minutes=2))
    assert done is not None and done.status == "executed"
    assert actions.executed_since(T0) == 1
    assert actions.finish(action.id, "failed", "again", T0) is None  # final states are final

    later = T0 + timedelta(minutes=3)
    late = actions.propose(incident_id, check("restart", "orders"), "x", "centinela-agent", later)
    assert late is not None
    [expired] = actions.expire(later + PENDING_TTL)
    assert expired.id == late.id and expired.status == "expired"

    kinds = [e["kind"] for e in store.events(incident_id) if e["kind"].startswith("action_")]
    assert kinds == [
        "action_proposed",
        "action_approved",
        "action_executed",
        "action_proposed",
        "action_expired",
    ]
    with store.pool.connection() as conn:
        row = conn.execute(
            "SELECT has_table_privilege('grafana_reader', 'incident_actions', 'SELECT') AS r, "
            "has_table_privilege('grafana_reader', 'incident_actions', 'UPDATE') AS w"
        ).fetchone()
    assert row is not None and row["r"] is True and row["w"] is False


def test_console_role_can_only_record_decisions(store: PostgresIncidentStore) -> None:
    import psycopg

    from centinela.actions.approvals import approve
    from centinela.actions.policy import check
    from centinela.actions.store import PostgresActionStore
    from centinela.incidents.store import CONSOLE_ROLE, make_pool

    password = uuid.uuid4().hex
    store.migrate(console_password=password)
    incident_id = store.create((SERVICE_DOWN,), T0)
    now = datetime.now(UTC)
    action = PostgresActionStore(store.pool).propose(
        incident_id, check("scale_up", "payments"), "down", "centinela-agent", now
    )
    assert action is not None

    url = f"user={CONSOLE_ROLE} password={password}"
    console = PostgresActionStore(make_pool(url))
    decided = approve(console, action.id, "Fabricio", now)  # the one thing it may do
    assert decided.status == "approved" and decided.decided_by == "Fabricio"

    forbidden = [
        "UPDATE incident_actions SET target = 'postgres'",  # cannot change WHAT runs
        """UPDATE incident_actions SET params = '{"replicas": 3}'""",
        "DELETE FROM incident_actions",  # cannot erase the audit trail
        "DELETE FROM incident_events",
        "UPDATE incidents SET root_cause = 'x'",
        "INSERT INTO incident_actions (incident_id, kind, target, reason, proposed_by, "
        "proposed_at, expires_at) VALUES (1, 'restart', 'orders', 'x', 'x', now(), now())",
    ]
    for statement in forbidden:
        with psycopg.connect(url) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)


def test_mcp_role_can_only_propose(store: PostgresIncidentStore) -> None:
    import psycopg

    from centinela.actions.policy import check
    from centinela.actions.store import PostgresActionStore
    from centinela.incidents.store import MCP_ROLE

    password = uuid.uuid4().hex
    store.migrate(mcp_password=password)
    incident_id = store.create((SERVICE_DOWN,), T0)
    url = f"user={MCP_ROLE} password={password}"
    mcp_store = PostgresActionStore(make_pool(url))
    action = mcp_store.propose(
        incident_id, check("restart", "payments"), "evidence", "mcp:test", datetime.now(UTC)
    )
    assert action is not None and action.status == "pending"  # always starts pending

    forbidden = [
        # a proposal that arrives already approved
        "INSERT INTO incident_actions (incident_id, kind, target, reason, proposed_by, "
        "proposed_at, expires_at, status) VALUES ((SELECT max(id) FROM incidents), 'restart', "
        "'orders', 'x', 'x', "
        "now(), now(), 'approved')",
        "UPDATE incident_actions SET status = 'approved'",  # approving one
        "DELETE FROM incident_actions",
        "UPDATE incidents SET root_cause = 'x'",
    ]
    for statement in forbidden:
        with psycopg.connect(url) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)


def test_reopen_and_recently_resolved(store: PostgresIncidentStore) -> None:
    detected = T0 + timedelta(minutes=1)
    incident_id = store.create((SERVICE_DOWN,), detected)
    store.resolve_incident(incident_id, detected + timedelta(minutes=4))
    [recent] = store.recently_resolved(detected)
    assert (recent.id, recent.started_at) == (incident_id, SERVICE_DOWN.active_at)
    assert store.recently_resolved(detected + timedelta(minutes=5)) == []
    store.reopen(incident_id, detected + timedelta(minutes=6), "late alert")
    [open_] = store.open_incidents()
    assert open_.id == incident_id
    store.reopen(incident_id, detected + timedelta(minutes=7), "again")  # no-op when open
    kinds = [e["kind"] for e in store.events(incident_id)]
    assert kinds.count("incident_reopened") == 1
