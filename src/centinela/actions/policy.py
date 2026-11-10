"""What the agent may ever do, decided here in code, not by the model.

An allowlist of action kinds and targets, hard limits on their parameters, and a rate limit
on how many actions run per hour. The model can only *suggest* from this list; the executor
checks the same policy again right before acting.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

# Stateless shop deployments. PostgreSQL is deliberately NOT here: restarting or scaling a
# database is a decision for a human with a plan, not for an agent.
TARGETS = ("catalog", "orders", "payments", "redis")
KINDS = ("scale_up", "restart")
MAX_REPLICAS = 3
PENDING_TTL = timedelta(minutes=15)
MAX_EXECUTIONS_PER_HOUR = 3


class PolicyError(ValueError):
    """The action is not allowed, whoever approves it."""


@dataclass(frozen=True)
class Proposal:
    kind: str
    target: str
    params: dict[str, Any]


def check(kind: str, target: str, params: dict[str, Any] | None = None) -> Proposal:
    params = dict(params or {})
    if kind not in KINDS:
        raise PolicyError(f"action {kind!r} is not allowed (allowed: {', '.join(KINDS)})")
    if target not in TARGETS:
        raise PolicyError(f"target {target!r} is not allowed (allowed: {', '.join(TARGETS)})")
    if kind == "scale_up":
        replicas = params.setdefault("replicas", 1)
        if not isinstance(replicas, int) or isinstance(replicas, bool):
            raise PolicyError("replicas must be an integer")
        if not 1 <= replicas <= MAX_REPLICAS:
            raise PolicyError(f"replicas must be between 1 and {MAX_REPLICAS}")
    elif params:
        raise PolicyError(f"{kind} takes no parameters")
    return Proposal(kind, target, params)
