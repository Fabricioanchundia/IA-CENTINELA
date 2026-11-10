"""Human decisions, shared by the CLI and the web page so both follow the same rules."""

import re
from datetime import datetime

from centinela.actions.models import Action
from centinela.actions.policy import PolicyError, check
from centinela.actions.store import ActionStore

MACHINE_NAMES = {"agent", "centinela-agent", "centinela", "executor", "system", "mcp"}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@-]{1,63}$")


class ApprovalError(ValueError):
    """The decision cannot be recorded (unknown action, already decided, expired...)."""


def _validate_person(by: str) -> str:
    by = by.strip()
    if not NAME.match(by):
        raise ApprovalError("give your name (2-64 letters, digits, spaces, . _ @ -)")
    if by.lower() in MACHINE_NAMES:
        raise ApprovalError("a human must decide: the agent cannot approve its own actions")
    return by


def _explain(store: ActionStore, action_id: int, now: datetime) -> ApprovalError:
    action = store.get(action_id)
    if action is None:
        return ApprovalError(f"action {action_id} does not exist")
    if action.status == "pending" and action.expires_at <= now:
        return ApprovalError(f"action {action_id} expired at {action.expires_at:%H:%M:%S} UTC")
    return ApprovalError(f"action {action_id} is already {action.status}")


def approve(
    store: ActionStore, action_id: int, by: str, now: datetime, note: str | None = None
) -> Action:
    by = _validate_person(by)
    current = store.get(action_id)
    if current is not None:
        try:
            check(current.kind, current.target, current.params)
        except PolicyError as exc:
            raise ApprovalError(f"cannot be approved: {exc}") from exc
    action = store.decide(action_id, True, by, note, now)
    if action is None:
        raise _explain(store, action_id, now)
    return action


def reject(store: ActionStore, action_id: int, by: str, note: str, now: datetime) -> Action:
    by = _validate_person(by)
    if not note.strip():
        raise ApprovalError("say why you reject it (--note): it goes into the postmortem")
    action = store.decide(action_id, False, by, note.strip()[:500], now)
    if action is None:
        raise _explain(store, action_id, now)
    return action
