"""Pure incident logic: given what is firing now and what is open, decide what changed.

No I/O here, so every rule is easy to unit test:

* A firing alert that is not part of any open incident joins the most recently started
  open incident (cascading failures share one incident: `payments` down also makes
  `orders` fail), or opens a new incident if nothing is open.
* An alert that stops firing is resolved inside its incident.
* An incident is resolved when none of its alerts is firing any more.
* A late alert reopens the incident it belongs to (Phase 9). Some alerts lag: HighLatencyP95
  averages 5 minutes of requests and waits 5 more, so it can fire minutes after the outage that
  caused it has resolved. If nothing is open, new alerts that became active while a recently
  resolved incident was still open reopen that incident; only the others open a new one. In the
  chaos run such echoes caused a false alarm (incident 16, after the PostgreSQL outage) and
  inflated the MTTD of the next incident (catalog down, 323 s instead of 53 s).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from centinela.incidents.models import Alert, OpenIncident, severity_rank


@dataclass(frozen=True)
class CreateIncident:
    alerts: tuple[Alert, ...]  # first one is the primary alert (earliest, most severe)


@dataclass(frozen=True)
class AttachAlert:
    incident_id: int
    alert: Alert


@dataclass(frozen=True)
class ResolveAlert:
    incident_id: int
    fingerprint: str


@dataclass(frozen=True)
class ResolveIncident:
    incident_id: int


@dataclass(frozen=True)
class ReopenIncident:
    incident_id: int


@dataclass(frozen=True)
class RecentIncident:
    """A resolved incident, candidate to absorb late alerts."""

    id: int
    started_at: datetime
    resolved_at: datetime


REOPEN_WINDOW = timedelta(minutes=10)

Action = CreateIncident | AttachAlert | ResolveAlert | ResolveIncident | ReopenIncident


def _primary_first(alerts: list[Alert]) -> list[Alert]:
    """Most severe first, then the one that started earliest."""
    return sorted(alerts, key=lambda a: (-severity_rank(a.severity), a.active_at))


def _late(
    alerts: list[Alert], recent: list[RecentIncident], now: datetime
) -> tuple[int | None, list[Alert]]:
    """Split new alerts: those that became active while a recently resolved incident was still
    open belong to it (late alerts); the rest are a new problem."""
    candidates = [r for r in recent if now - r.resolved_at <= REOPEN_WINDOW]
    if not candidates:
        return None, []
    last = max(candidates, key=lambda r: r.resolved_at)
    late = [a for a in alerts if last.started_at <= a.active_at <= last.resolved_at]
    return (last.id, late) if late else (None, [])


def plan(
    firing: list[Alert],
    open_incidents: list[OpenIncident],
    now: datetime,
    recently_resolved: list[RecentIncident] | None = None,
) -> list[Action]:
    actions: list[Action] = []
    firing_by_fp = {a.fingerprint: a for a in firing}
    known = {fp for inc in open_incidents for fp in inc.active_alerts}

    new_alerts = [a for fp, a in firing_by_fp.items() if fp not in known]
    if new_alerts:
        if open_incidents:
            target = max(open_incidents, key=lambda i: i.started_at)
            actions += [AttachAlert(target.id, a) for a in _primary_first(new_alerts)]
        else:
            owner, late = _late(new_alerts, recently_resolved or [], now)
            if owner is not None:
                # The old incident was not really over: reopen it with its late alerts, so they
                # neither raise a false alarm nor inflate the MTTD of a new incident.
                actions.append(ReopenIncident(owner))
                actions += [AttachAlert(owner, a) for a in _primary_first(late)]
            fresh = [a for a in new_alerts if a not in late]
            if fresh:
                actions.append(CreateIncident(tuple(_primary_first(fresh))))

    attached_to: dict[int, int] = {}
    for action in actions:
        if isinstance(action, AttachAlert):
            attached_to[action.incident_id] = attached_to.get(action.incident_id, 0) + 1

    for inc in open_incidents:
        stopped = sorted(fp for fp in inc.active_alerts if fp not in firing_by_fp)
        actions += [ResolveAlert(inc.id, fp) for fp in stopped]
        still_firing = len(inc.active_alerts) - len(stopped) + attached_to.get(inc.id, 0)
        if still_firing == 0:
            actions.append(ResolveIncident(inc.id))
    return actions
