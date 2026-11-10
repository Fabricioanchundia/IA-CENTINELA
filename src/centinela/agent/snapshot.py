"""Record and replay exactly what the model saw (Phase 9).

Every stored diagnosis now carries a JSON snapshot of its evidence. That serves two purposes:

- **Audit:** months later you can see what the agent knew when it proposed an action.
- **Evaluation:** a chaos drill in the cluster becomes a test case. `centinela eval run` feeds the
  same evidence to another model, another prompt or new guards, on a laptop with only Ollama,
  and compares the answers with the known fault. No cluster needed to re-run the past.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Any

from centinela.agent.evidence import Evidence, Item
from centinela.agent.guard import Finding
from centinela.agent.k8s import Workload
from centinela.incidents.models import Incident, IncidentAlert

VERSION = 1


def _ts(at: datetime | None) -> str | None:
    return at.isoformat() if at else None


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def _req_dt(value: Any) -> datetime:
    at = _dt(value)
    if at is None:
        raise ValueError("missing timestamp in evidence snapshot")
    return at


def to_json(ev: Evidence) -> dict[str, Any]:
    inc = ev.incident
    return {
        "version": VERSION,
        "incident": {
            **asdict(inc),
            "started_at": _ts(inc.started_at),
            "detected_at": _ts(inc.detected_at),
            "resolved_at": _ts(inc.resolved_at),
        },
        "alerts": [
            {
                **asdict(a),
                "active_at": _ts(a.active_at),
                "resolved_at": _ts(a.resolved_at),
            }
            for a in ev.alerts
        ],
        "observed_at": _ts(ev.observed_at),
        "items": [{**asdict(i), "services": sorted(i.services)} for i in ev.items],
        "workloads": [{**asdict(w), "rollout_at": _ts(w.rollout_at)} for w in ev.workloads],
        "notes": list(ev.notes),
        "quarantined": [asdict(q) for q in ev.quarantined],
    }


def from_json(data: dict[str, Any]) -> Evidence:
    if data.get("version") != VERSION:
        raise ValueError(f"unsupported evidence snapshot version {data.get('version')!r}")
    i = data["incident"]
    incident = Incident(
        id=int(i["id"]),
        title=str(i["title"]),
        category=str(i["category"]),
        service=i.get("service"),
        severity=str(i["severity"]),
        status=str(i["status"]),
        started_at=_req_dt(i["started_at"]),
        detected_at=_req_dt(i["detected_at"]),
        resolved_at=_dt(i.get("resolved_at")),
        root_cause=i.get("root_cause"),
        resolution=i.get("resolution"),
    )
    return Evidence(
        incident=incident,
        alerts=[
            IncidentAlert(
                name=str(a["name"]),
                severity=str(a["severity"]),
                labels={str(k): str(v) for k, v in a["labels"].items()},
                summary=str(a["summary"]),
                active_at=_req_dt(a["active_at"]),
                resolved_at=_dt(a.get("resolved_at")),
            )
            for a in data["alerts"]
        ],
        observed_at=_req_dt(data["observed_at"]),
        items=[
            Item(
                str(it["id"]),
                str(it["kind"]),
                str(it["title"]),
                str(it["body"]),
                frozenset(it.get("services", [])),
            )
            for it in data["items"]
        ],
        workloads=[
            Workload(
                str(w["kind"]),
                str(w["name"]),
                int(w["desired"]),
                int(w["ready"]),
                _dt(w.get("rollout_at")),
                w.get("revision"),
            )
            for w in data["workloads"]
        ],
        notes=[str(n) for n in data.get("notes", [])],
        quarantined=[
            Finding(str(q["where"]), tuple(q["rules"]), int(q["chars"]))
            for q in data.get("quarantined", [])
        ],
    )


class ReplayBuilder:
    """Stands in for EvidenceBuilder: always returns (a fresh copy of) the recorded evidence."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot

    def build(self, incident: Incident) -> Evidence:
        return from_json(self.snapshot)


class ReplayStore:
    """Just enough of an IncidentStore for Diagnoser.diagnose(store_result=False)."""

    def __init__(self, incident: Incident) -> None:
        self.incident = incident

    def get(self, incident_id: int) -> Incident | None:
        return self.incident if incident_id == self.incident.id else None
