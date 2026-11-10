"""An action moves through a small state machine. Only a human moves it out of `pending`.

pending --approve--> approved --executor--> executed | failed | skipped
   |  \\--reject---> rejected
   \\---15 min------> expired
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
EXECUTED, FAILED, EXPIRED, SKIPPED = "executed", "failed", "expired", "skipped"
OPEN_STATES = (PENDING, APPROVED)
FINAL_STATES = (REJECTED, EXECUTED, FAILED, EXPIRED, SKIPPED)


@dataclass
class Action:
    id: int
    incident_id: int
    kind: str  # scale_up | restart
    target: str  # a shop deployment
    params: dict[str, Any]
    reason: str
    proposed_by: str
    status: str
    proposed_at: datetime
    expires_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    executed_at: datetime | None = None
    result: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        if self.kind == "scale_up":
            return f"scale {self.target} to {self.params.get('replicas', 1)} replica(s)"
        if self.kind == "restart":
            return f"rolling restart of {self.target}"
        return f"{self.kind} {self.target}"
