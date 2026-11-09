"""Where incidents live: PostgreSQL in the cluster, an in-memory fake for tests."""

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from psycopg import Connection, sql
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from centinela.incidents.models import Alert, Incident, OpenIncident, severity_rank

log = logging.getLogger("centinela.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id          BIGSERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    category    TEXT NOT NULL,
    service     TEXT,
    severity    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    source      TEXT NOT NULL DEFAULT 'alert',
    started_at  TIMESTAMPTZ NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ,
    root_cause  TEXT,
    resolution  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS incidents_status_idx ON incidents (status);
CREATE INDEX IF NOT EXISTS incidents_detected_idx ON incidents (detected_at);

CREATE TABLE IF NOT EXISTS incident_alerts (
    incident_id    BIGINT NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    fingerprint    TEXT NOT NULL,
    alertname      TEXT NOT NULL,
    severity       TEXT NOT NULL,
    labels         JSONB NOT NULL,
    summary        TEXT NOT NULL,
    active_at      TIMESTAMPTZ NOT NULL,
    seen_firing_at TIMESTAMPTZ NOT NULL,
    resolved_at    TIMESTAMPTZ,
    PRIMARY KEY (incident_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS incident_events (
    id          BIGSERIAL PRIMARY KEY,
    incident_id BIGINT NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    at          TIMESTAMPTZ NOT NULL,
    kind        TEXT NOT NULL,
    payload     JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS incident_events_incident_idx ON incident_events (incident_id, at);

CREATE OR REPLACE VIEW incident_metrics AS
SELECT id, title, category, service, severity, status, source,
       started_at, detected_at, resolved_at, root_cause, resolution,
       EXTRACT(EPOCH FROM detected_at - started_at) AS mttd_seconds,
       EXTRACT(EPOCH FROM resolved_at - detected_at) AS mttr_seconds
FROM incidents;
"""

READER_ROLE = "grafana_reader"

INCIDENT_COLUMNS = (
    "id, title, category, service, severity, status, started_at, detected_at, "
    "resolved_at, root_cause, resolution"
)


class IncidentStore(Protocol):
    def open_incidents(self) -> list[OpenIncident]: ...
    def create(self, alerts: tuple[Alert, ...], detected_at: datetime) -> int: ...
    def attach(self, incident_id: int, alert: Alert, seen_at: datetime) -> None: ...
    def resolve_alert(self, incident_id: int, fingerprint: str, at: datetime) -> None: ...
    def resolve_incident(self, incident_id: int, at: datetime) -> None: ...
    def add_event(
        self, incident_id: int, kind: str, payload: dict[str, Any], at: datetime
    ) -> None: ...
    def recent(self, limit: int = 20) -> list[Incident]: ...
    def get(self, incident_id: int) -> Incident | None: ...
    def events(self, incident_id: int) -> list[dict[str, Any]]: ...
    def close(self, incident_id: int, root_cause: str, resolution: str) -> bool: ...


# ---------------------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------------------
Pool = ConnectionPool[Connection[DictRow]]


def make_pool(database_url: str = "") -> Pool:
    """Empty URL = libpq reads PGHOST, PGUSER, PGPASSWORD, PGDATABASE from the environment."""
    return ConnectionPool(
        database_url,
        connection_class=Connection[DictRow],
        kwargs={"row_factory": dict_row},
        min_size=1,
        max_size=3,
        open=True,
        timeout=10,
    )


class PostgresIncidentStore:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    def migrate(self, reader_password: str | None = None) -> None:
        """Create tables and a read-only role for Grafana (idempotent)."""
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)
            if reader_password:
                exists = conn.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = %s", (READER_ROLE,)
                ).fetchone()
                verb = "ALTER" if exists else "CREATE"
                conn.execute(
                    sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(READER_ROLE), sql.Literal(reader_password)
                    )
                )
                db = conn.execute("SELECT current_database() AS db").fetchone()
                assert db is not None  # noqa: S101
                conn.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(db["db"]), sql.Identifier(READER_ROLE)
                    )
                )
                conn.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                        sql.Identifier(READER_ROLE)
                    )
                )
                conn.execute(
                    sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(
                        sql.Identifier(READER_ROLE)
                    )
                )
        log.info("incident schema ready")

    def open_incidents(self) -> list[OpenIncident]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT i.id, i.severity, i.started_at, "
                "       COALESCE(array_agg(a.fingerprint) FILTER "
                "                (WHERE a.resolved_at IS NULL), '{}') AS active "
                "FROM incidents i LEFT JOIN incident_alerts a ON a.incident_id = i.id "
                "WHERE i.status = 'open' GROUP BY i.id ORDER BY i.started_at"
            ).fetchall()
        return [
            OpenIncident(r["id"], r["severity"], r["started_at"], set(r["active"])) for r in rows
        ]

    def create(self, alerts: tuple[Alert, ...], detected_at: datetime) -> int:
        primary = alerts[0]
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO incidents (title, category, service, severity, started_at, "
                "detected_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    primary.summary,
                    primary.name,
                    primary.service,
                    primary.severity,
                    min(a.active_at for a in alerts),
                    detected_at,
                ),
            ).fetchone()
        assert row is not None  # noqa: S101
        incident_id = int(row["id"])
        for alert in alerts:
            self.attach(incident_id, alert, detected_at)
        return incident_id

    def attach(self, incident_id: int, alert: Alert, seen_at: datetime) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO incident_alerts (incident_id, fingerprint, alertname, severity, "
                "labels, summary, active_at, seen_firing_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (incident_id, fingerprint) "
                "DO UPDATE SET resolved_at = NULL, seen_firing_at = EXCLUDED.seen_firing_at",
                (
                    incident_id,
                    alert.fingerprint,
                    alert.name,
                    alert.severity,
                    json.dumps(alert.labels),
                    alert.summary,
                    alert.active_at,
                    seen_at,
                ),
            )
            # The incident takes the worst severity of its alerts
            conn.execute(
                "UPDATE incidents SET severity = %s WHERE id = %s AND "
                "(CASE severity WHEN 'critical' THEN 2 WHEN 'warning' THEN 1 ELSE 0 END) < %s",
                (alert.severity, incident_id, severity_rank(alert.severity)),
            )
        self.add_event(
            incident_id, "alert_firing", {"alert": alert.name, "labels": alert.labels}, seen_at
        )

    def resolve_alert(self, incident_id: int, fingerprint: str, at: datetime) -> None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "UPDATE incident_alerts SET resolved_at = %s "
                "WHERE incident_id = %s AND fingerprint = %s AND resolved_at IS NULL "
                "RETURNING alertname",
                (at, incident_id, fingerprint),
            ).fetchone()
        if row:
            self.add_event(incident_id, "alert_resolved", {"alert": row["alertname"]}, at)

    def resolve_incident(self, incident_id: int, at: datetime) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE incidents SET status = 'resolved', resolved_at = %s "
                "WHERE id = %s AND status = 'open'",
                (at, incident_id),
            )
        self.add_event(incident_id, "incident_resolved", {}, at)

    def add_event(self, incident_id: int, kind: str, payload: dict[str, Any], at: datetime) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO incident_events (incident_id, at, kind, payload) "
                "VALUES (%s, %s, %s, %s)",
                (incident_id, at, kind, json.dumps(payload)),
            )

    def recent(self, limit: int = 20) -> list[Incident]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {INCIDENT_COLUMNS} FROM incidents ORDER BY id DESC LIMIT %s",  # noqa: S608
                (limit,),
            ).fetchall()
        return [Incident(**r) for r in rows]

    def get(self, incident_id: int) -> Incident | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                f"SELECT {INCIDENT_COLUMNS} FROM incidents WHERE id = %s",  # noqa: S608
                (incident_id,),
            ).fetchone()
        return Incident(**row) if row else None

    def events(self, incident_id: int) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT at, kind, payload FROM incident_events WHERE incident_id = %s "
                "ORDER BY at, id",
                (incident_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self, incident_id: int, root_cause: str, resolution: str) -> bool:
        with self.pool.connection() as conn:
            row = conn.execute(
                "UPDATE incidents SET root_cause = %s, resolution = %s WHERE id = %s RETURNING id",
                (root_cause, resolution, incident_id),
            ).fetchone()
        return row is not None


# ---------------------------------------------------------------------------------------
# In-memory fake (unit tests)
# ---------------------------------------------------------------------------------------
@dataclass
class _Row:
    incident: Incident
    alerts: dict[str, Alert] = field(default_factory=dict)
    resolved_alerts: set[str] = field(default_factory=set)
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class InMemoryIncidentStore:
    rows: dict[int, _Row] = field(default_factory=dict)

    def open_incidents(self) -> list[OpenIncident]:
        return [
            OpenIncident(
                i,
                r.incident.severity,
                r.incident.started_at,
                set(r.alerts) - r.resolved_alerts,
            )
            for i, r in sorted(self.rows.items())
            if r.incident.status == "open"
        ]

    def create(self, alerts: tuple[Alert, ...], detected_at: datetime) -> int:
        primary = alerts[0]
        incident_id = len(self.rows) + 1
        self.rows[incident_id] = _Row(
            Incident(
                id=incident_id,
                title=primary.summary,
                category=primary.name,
                service=primary.service,
                severity=primary.severity,
                status="open",
                started_at=min(a.active_at for a in alerts),
                detected_at=detected_at,
                resolved_at=None,
            )
        )
        for alert in alerts:
            self.attach(incident_id, alert, detected_at)
        return incident_id

    def attach(self, incident_id: int, alert: Alert, seen_at: datetime) -> None:
        row = self.rows[incident_id]
        row.alerts[alert.fingerprint] = alert
        row.resolved_alerts.discard(alert.fingerprint)
        if severity_rank(alert.severity) > severity_rank(row.incident.severity):
            row.incident = replace(row.incident, severity=alert.severity)
        self.add_event(incident_id, "alert_firing", {"alert": alert.name}, seen_at)

    def resolve_alert(self, incident_id: int, fingerprint: str, at: datetime) -> None:
        row = self.rows[incident_id]
        if fingerprint in row.alerts and fingerprint not in row.resolved_alerts:
            row.resolved_alerts.add(fingerprint)
            self.add_event(
                incident_id, "alert_resolved", {"alert": row.alerts[fingerprint].name}, at
            )

    def resolve_incident(self, incident_id: int, at: datetime) -> None:
        row = self.rows[incident_id]
        row.incident = replace(row.incident, status="resolved", resolved_at=at)
        self.add_event(incident_id, "incident_resolved", {}, at)

    def add_event(self, incident_id: int, kind: str, payload: dict[str, Any], at: datetime) -> None:
        self.rows[incident_id].events.append({"at": at, "kind": kind, "payload": payload})

    def recent(self, limit: int = 20) -> list[Incident]:
        return [r.incident for _, r in sorted(self.rows.items(), reverse=True)][:limit]

    def get(self, incident_id: int) -> Incident | None:
        row = self.rows.get(incident_id)
        return row.incident if row else None

    def events(self, incident_id: int) -> list[dict[str, Any]]:
        return list(self.rows[incident_id].events)

    def close(self, incident_id: int, root_cause: str, resolution: str) -> bool:
        row = self.rows.get(incident_id)
        if row is None:
            return False
        row.incident = replace(row.incident, root_cause=root_cause, resolution=resolution)
        return True
