"""Incident collector: polls Prometheus, keeps the incident history up to date.

    centinela collect

Each cycle: read firing alerts -> plan() decides what changed -> apply it to the store,
attaching recent error logs from Loki as evidence when an incident opens or grows.
"""

import logging
import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import FrameType
from typing import Protocol

import httpx
from prometheus_client import Counter, Gauge

from centinela.incidents.models import Alert
from centinela.incidents.store import IncidentStore
from centinela.incidents.tracker import (
    REOPEN_WINDOW,
    Action,
    AttachAlert,
    CreateIncident,
    ReopenIncident,
    ResolveAlert,
    ResolveIncident,
    plan,
)

log = logging.getLogger("centinela.collector")

POLLS = Counter("centinela_collector_polls_total", "Collector cycles by result.", ["result"])
OPEN_INCIDENTS = Gauge("centinela_incidents_open", "Incidents currently open.")
FIRING_ALERTS = Gauge("centinela_alerts_firing", "Alerts firing in the last cycle.")
INCIDENTS_CREATED = Counter("centinela_incidents_created_total", "Incidents opened.")
INCIDENTS_RESOLVED = Counter("centinela_incidents_resolved_total", "Incidents resolved.")

LOG_LOOKBACK = timedelta(minutes=2)


class AlertSource(Protocol):
    def firing(self) -> list[Alert]: ...


class LogSource(Protocol):
    def recent(
        self, service: str | None, start: datetime, end: datetime, limit: int = 20
    ) -> list[dict[str, object]]: ...


class Collector:
    def __init__(
        self,
        alerts: AlertSource,
        store: IncidentStore,
        logs: LogSource | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.alerts = alerts
        self.store = store
        self.logs = logs
        self.clock = clock

    def cycle(self) -> list[Action]:
        now = self.clock()
        firing = self.alerts.firing()
        actions = plan(
            firing,
            self.store.open_incidents(),
            now,
            self.store.recently_resolved(now - REOPEN_WINDOW),
        )
        for action in actions:
            self._apply(action, now)
        FIRING_ALERTS.set(len(firing))
        OPEN_INCIDENTS.set(len(self.store.open_incidents()))
        return actions

    def _apply(self, action: Action, now: datetime) -> None:
        if isinstance(action, CreateIncident):
            incident_id = self.store.create(action.alerts, now)
            INCIDENTS_CREATED.inc()
            primary = action.alerts[0]
            log.warning(
                "incident opened",
                extra={"fields": {"incident_id": incident_id, "alert": primary.name}},
            )
            for alert in action.alerts:
                self._attach_logs(incident_id, alert, now)
        elif isinstance(action, AttachAlert):
            self.store.attach(action.incident_id, action.alert, now)
            self._attach_logs(action.incident_id, action.alert, now)
        elif isinstance(action, ResolveAlert):
            self.store.resolve_alert(action.incident_id, action.fingerprint, now)
        elif isinstance(action, ReopenIncident):
            self.store.reopen(
                action.incident_id,
                now,
                "a late alert that became active while this incident was open",
            )
            log.warning(
                "incident reopened by a late alert",
                extra={"fields": {"incident_id": action.incident_id}},
            )
        elif isinstance(action, ResolveIncident):
            self.store.resolve_incident(action.incident_id, now)
            INCIDENTS_RESOLVED.inc()
            log.info("incident resolved", extra={"fields": {"incident_id": action.incident_id}})

    def _attach_logs(self, incident_id: int, alert: Alert, now: datetime) -> None:
        if self.logs is None:
            return
        try:
            lines = self.logs.recent(alert.service, alert.active_at - LOG_LOOKBACK, now)
        except Exception as exc:  # evidence is best effort: never lose the incident
            log.warning("could not fetch logs", extra={"fields": {"error": repr(exc)}})
            return
        if lines:
            self.store.add_event(
                incident_id,
                "logs",
                {"alert": alert.name, "service": alert.service, "lines": lines},
                now,
            )

    def run_forever(self, interval_s: float) -> None:
        stop = {"now": False}

        def handle_stop(signum: int, frame: FrameType | None) -> None:
            stop["now"] = True

        signal.signal(signal.SIGTERM, handle_stop)
        log.info("collector started", extra={"fields": {"interval_s": interval_s}})
        while not stop["now"]:
            started = time.monotonic()
            try:
                self.cycle()
                POLLS.labels("ok").inc()
            except httpx.HTTPError as exc:  # Prometheus restarting: expected, keep it short
                POLLS.labels("error").inc()
                log.warning("prometheus unreachable", extra={"fields": {"error": repr(exc)}})
            except Exception:
                POLLS.labels("error").inc()
                log.exception("collector cycle failed")
            time.sleep(max(0.0, interval_s - (time.monotonic() - started)))
        log.info("collector stopped")
