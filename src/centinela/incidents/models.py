"""Domain types shared by the tracker, the stores and the collector."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def severity_rank(severity: str) -> int:
    return SEVERITY_RANK.get(severity, 0)


def fingerprint(labels: dict[str, str]) -> str:
    """Stable identity of an alert: same alert name and labels -> same fingerprint."""
    canonical = json.dumps(labels, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Alert:
    """A firing Prometheus alert."""

    fingerprint: str
    name: str
    severity: str
    labels: dict[str, str]
    summary: str
    active_at: datetime  # when the alert condition first became true (start of the fault)

    @property
    def service(self) -> str | None:
        return self.labels.get("service") or self.labels.get("upstream")

    @classmethod
    def from_prometheus(cls, raw: dict[str, Any]) -> "Alert":
        labels = {str(k): str(v) for k, v in raw.get("labels", {}).items()}
        annotations = raw.get("annotations", {})
        return cls(
            fingerprint=fingerprint(labels),
            name=labels.get("alertname", "unknown"),
            severity=labels.get("severity", "warning"),
            labels=labels,
            summary=str(annotations.get("summary") or labels.get("alertname", "alert")),
            active_at=datetime.fromisoformat(str(raw["activeAt"])),
        )


@dataclass
class OpenIncident:
    """An incident that still has at least one firing alert."""

    id: int
    severity: str
    started_at: datetime
    active_alerts: set[str] = field(default_factory=set)  # fingerprints still firing


@dataclass
class Incident:
    id: int
    title: str
    category: str
    service: str | None
    severity: str
    status: str
    started_at: datetime
    detected_at: datetime
    resolved_at: datetime | None
    root_cause: str | None = None
    resolution: str | None = None

    @property
    def mttd_seconds(self) -> float:
        """Mean Time To Detect for this incident: fault start -> Centinela knows."""
        return (self.detected_at - self.started_at).total_seconds()

    @property
    def mttr_seconds(self) -> float | None:
        """Mean Time To Resolve for this incident: detection -> all alerts cleared."""
        if self.resolved_at is None:
            return None
        return (self.resolved_at - self.detected_at).total_seconds()
