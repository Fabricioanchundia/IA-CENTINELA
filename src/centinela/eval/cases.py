"""Evaluation cases: recorded evidence plus the known answer (Phase 9).

A case is one JSON file in `eval/cases/`:

    {"name": "...", "source": "chaos" | "synthetic", "fault": "what was really broken",
     "expected": {"root_cause": [...], "category": [...], "actions": ["scale_up:payments", ...]},
     "evidence": {...snapshot...}, "live": {...what happened in the cluster, chaos only...}}

`expected.actions` lists the proposals that would be right; "none" means "no automatic action"
(the right fix is outside the allowlist, e.g. a rollback or anything on the database).
`root_cause` and `category` can list several acceptable answers when the fault is genuinely
ambiguous; the evaluation reports how often the model hits any of them.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from centinela.actions.store import ActionStore
from centinela.incidents.store import IncidentStore


@dataclass(frozen=True)
class Case:
    name: str
    source: str
    fault: str
    root_cause: tuple[str, ...]
    category: tuple[str, ...]
    actions: tuple[str, ...]  # "kind:target" or "none"
    evidence: dict[str, Any]
    live: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "fault": self.fault,
            "expected": {
                "root_cause": list(self.root_cause),
                "category": list(self.category),
                "actions": list(self.actions),
            },
            "evidence": self.evidence,
            "live": self.live,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Case":
        exp = data["expected"]
        return cls(
            name=str(data["name"]),
            source=str(data.get("source", "?")),
            fault=str(data.get("fault", "")),
            root_cause=tuple(exp["root_cause"]),
            category=tuple(exp["category"]),
            actions=tuple(exp["actions"]),
            evidence=dict(data["evidence"]),
            live=dict(data.get("live") or {}),
        )


def load(directory: Path) -> list[Case]:
    return [Case.from_json(json.loads(p.read_text())) for p in sorted(directory.glob("*.json"))]


def _split(value: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in value.split(",") if v.strip())


class NotDiagnosedError(LookupError):
    """The incident has no stored diagnosis (with evidence) yet."""


def from_incident(
    incidents: IncidentStore,
    actions: ActionStore,
    incident_id: int,
    name: str,
    fault: str,
    root_cause: str,
    category: str,
    expected_actions: str,
) -> Case:
    """Turn an incident from a chaos drill into a case (runs in the cluster, next to the DB)."""
    incident = incidents.get(incident_id)
    if incident is None:
        raise LookupError(f"incident {incident_id} not found")
    diagnoses = [e for e in incidents.events(incident_id) if e["kind"] == "diagnosis"]
    recorded = [e for e in diagnoses if "evidence" in e["payload"]]
    if not recorded:
        raise NotDiagnosedError(f"incident {incident_id} has no diagnosis with evidence yet")
    first = recorded[0]
    report = first["payload"]
    at: datetime = first["at"]
    proposals = [a for a in actions.recent(200) if a.incident_id == incident_id]
    d = report["diagnosis"]
    live = {
        "incident_id": incident_id,
        "title": incident.title,
        "mttd_seconds": round(incident.mttd_seconds, 1),
        "detected_to_diagnosis_seconds": round((at - incident.detected_at).total_seconds(), 1),
        "model": report.get("model"),
        "model_seconds": report.get("seconds"),
        "root_cause": d["root_cause_service"],
        "category": d["category"],
        "action": f"{d['recommended_action']}:{d['action_target']}",
        "valid": report.get("valid"),
        "baseline": report.get("baseline_service"),
        "proposals": [f"{a.kind}:{a.target}" for a in proposals],
        "summary": d["summary"],
    }
    return Case(
        name=name,
        source="chaos",
        fault=fault,
        root_cause=_split(root_cause),
        category=_split(category),
        actions=_split(expected_actions),
        evidence=report["evidence"],
        live=live,
    )
