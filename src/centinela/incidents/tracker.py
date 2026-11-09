"""Pure incident logic: given what is firing now and what is open, decide what changed.

No I/O here, so every rule is easy to unit test:

* A firing alert that is not part of any open incident joins the most recently started
  open incident (cascading failures share one incident: `payments` down also makes
  `orders` fail), or opens a new incident if nothing is open.
* An alert that stops firing is resolved inside its incident.
* An incident is resolved when none of its alerts is firing any more.
"""

from dataclasses import dataclass
from datetime import datetime

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


Action = CreateIncident | AttachAlert | ResolveAlert | ResolveIncident


def _primary_first(alerts: list[Alert]) -> list[Alert]:
    """Most severe first, then the one that started earliest."""
    return sorted(alerts, key=lambda a: (-severity_rank(a.severity), a.active_at))


def plan(firing: list[Alert], open_incidents: list[OpenIncident], now: datetime) -> list[Action]:
    del now  # kept in the signature: future rules (flap damping) depend on time
    actions: list[Action] = []
    firing_by_fp = {a.fingerprint: a for a in firing}
    known = {fp for inc in open_incidents for fp in inc.active_alerts}

    new_alerts = [a for fp, a in firing_by_fp.items() if fp not in known]
    if new_alerts:
        if open_incidents:
            target = max(open_incidents, key=lambda i: i.started_at)
            actions += [AttachAlert(target.id, a) for a in _primary_first(new_alerts)]
        else:
            actions.append(CreateIncident(tuple(_primary_first(new_alerts))))

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
