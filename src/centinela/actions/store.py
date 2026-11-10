"""Where actions live. Every state change is one atomic UPDATE guarded by the current state,
so two people (or the CLI and the web page) can never both decide the same action, and a
decision on an expired action is impossible. Each change is also written to the incident
timeline (`incident_events`) as the audit trail."""

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from psycopg import Connection, errors
from psycopg.rows import DictRow

from centinela.actions.models import (
    APPROVED,
    EXPIRED,
    OPEN_STATES,
    PENDING,
    Action,
)
from centinela.actions.policy import PENDING_TTL, Proposal
from centinela.incidents.store import Pool

COLUMNS = (
    "id, incident_id, kind, target, params, reason, proposed_by, status, proposed_at, "
    "expires_at, decided_by, decided_at, decision_note, executed_at, result"
)


class ActionStore(Protocol):
    def propose(
        self, incident_id: int, proposal: Proposal, reason: str, by: str, now: datetime
    ) -> Action | None: ...
    def get(self, action_id: int) -> Action | None: ...
    def recent(self, limit: int = 20, open_only: bool = False) -> list[Action]: ...
    def decide(
        self, action_id: int, approve: bool, by: str, note: str | None, now: datetime
    ) -> Action | None: ...
    def expire(self, now: datetime) -> list[Action]: ...
    def approved(self) -> list[Action]: ...
    def finish(self, action_id: int, status: str, result: str, now: datetime) -> Action | None: ...
    def executed_since(self, since: datetime) -> int: ...


def _event(action: Action, kind: str) -> dict[str, Any]:
    return {
        "action_id": action.id,
        "action": action.describe(),
        "status": action.status,
        "by": action.decided_by or action.proposed_by,
        "note": action.decision_note,
        "result": action.result,
    }


class PostgresActionStore:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    @staticmethod
    def _audit(conn: Connection[DictRow], action: Action, kind: str, at: datetime) -> None:
        conn.execute(
            "INSERT INTO incident_events (incident_id, at, kind, payload) VALUES (%s, %s, %s, %s)",
            (action.incident_id, at, kind, json.dumps(_event(action, kind))),
        )

    def _one(self, sql: str, params: tuple[Any, ...], event: str, at: datetime) -> Action | None:
        with self.pool.connection() as conn:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            action = Action(**row)
            self._audit(conn, action, event, at)
            return action

    def propose(
        self, incident_id: int, proposal: Proposal, reason: str, by: str, now: datetime
    ) -> Action | None:
        try:
            return self._one(
                "INSERT INTO incident_actions (incident_id, kind, target, params, reason, "  # noqa: S608 - only the fixed column list is interpolated
                "proposed_by, proposed_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {COLUMNS}",
                (
                    incident_id,
                    proposal.kind,
                    proposal.target,
                    json.dumps(proposal.params),
                    reason,
                    by,
                    now,
                    now + PENDING_TTL,
                ),
                "action_proposed",
                now,
            )
        except errors.UniqueViolation:
            return None  # this incident already has an open action

    def get(self, action_id: int) -> Action | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                f"SELECT {COLUMNS} FROM incident_actions WHERE id = %s",  # noqa: S608
                (action_id,),
            ).fetchone()
        return Action(**row) if row else None

    def recent(self, limit: int = 20, open_only: bool = False) -> list[Action]:
        where = "WHERE status IN ('pending', 'approved') " if open_only else ""
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {COLUMNS} FROM incident_actions {where}ORDER BY id DESC LIMIT %s",  # noqa: S608
                (limit,),
            ).fetchall()
        return [Action(**r) for r in rows]

    def decide(
        self, action_id: int, approve: bool, by: str, note: str | None, now: datetime
    ) -> Action | None:
        status = APPROVED if approve else "rejected"
        return self._one(
            "UPDATE incident_actions SET status = %s, decided_by = %s, decided_at = %s, "  # noqa: S608 - only the fixed column list is interpolated
            "decision_note = %s WHERE id = %s AND status = 'pending' AND expires_at > %s "
            f"RETURNING {COLUMNS}",
            (status, by, now, note, action_id, now),
            f"action_{status}",
            now,
        )

    def expire(self, now: datetime) -> list[Action]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "UPDATE incident_actions SET status = 'expired', result = 'nobody decided in time' "  # noqa: S608 - only the fixed column list is interpolated
                f"WHERE status = 'pending' AND expires_at <= %s RETURNING {COLUMNS}",  # noqa: S608
                (now,),
            ).fetchall()
            actions = [Action(**r) for r in rows]
            for action in actions:
                self._audit(conn, action, "action_expired", now)
        return actions

    def approved(self) -> list[Action]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {COLUMNS} FROM incident_actions WHERE status = 'approved' "  # noqa: S608
                "ORDER BY decided_at"
            ).fetchall()
        return [Action(**r) for r in rows]

    def finish(self, action_id: int, status: str, result: str, now: datetime) -> Action | None:
        return self._one(
            "UPDATE incident_actions SET status = %s, result = %s, executed_at = %s "  # noqa: S608 - only the fixed column list is interpolated
            f"WHERE id = %s AND status = 'approved' RETURNING {COLUMNS}",
            (status, result, now, action_id),
            f"action_{status}",
            now,
        )

    def executed_since(self, since: datetime) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM incident_actions "
                "WHERE status = 'executed' AND executed_at >= %s",
                (since,),
            ).fetchone()
        return int(row["n"]) if row else 0


@dataclass
class InMemoryActionStore:
    """Same rules as PostgreSQL, for unit tests."""

    actions: dict[int, Action] = field(default_factory=dict)
    events: list[tuple[int, str, dict[str, Any]]] = field(default_factory=list)

    def _save(self, action: Action, event: str) -> Action:
        self.actions[action.id] = action
        self.events.append((action.incident_id, event, _event(action, event)))
        return action

    def propose(
        self, incident_id: int, proposal: Proposal, reason: str, by: str, now: datetime
    ) -> Action | None:
        if any(
            a.incident_id == incident_id and a.status in OPEN_STATES for a in self.actions.values()
        ):
            return None
        action = Action(
            len(self.actions) + 1,
            incident_id,
            proposal.kind,
            proposal.target,
            dict(proposal.params),
            reason,
            by,
            PENDING,
            now,
            now + PENDING_TTL,
        )
        return self._save(action, "action_proposed")

    def get(self, action_id: int) -> Action | None:
        return self.actions.get(action_id)

    def recent(self, limit: int = 20, open_only: bool = False) -> list[Action]:
        items = sorted(self.actions.values(), key=lambda a: a.id, reverse=True)
        return [a for a in items if not open_only or a.status in OPEN_STATES][:limit]

    def decide(
        self, action_id: int, approve: bool, by: str, note: str | None, now: datetime
    ) -> Action | None:
        a = self.actions.get(action_id)
        if a is None or a.status != PENDING or a.expires_at <= now:
            return None
        status = APPROVED if approve else "rejected"
        new = replace(a, status=status, decided_by=by, decided_at=now, decision_note=note)
        return self._save(new, f"action_{status}")

    def expire(self, now: datetime) -> list[Action]:
        out = []
        for a in list(self.actions.values()):
            if a.status == PENDING and a.expires_at <= now:
                new = replace(a, status=EXPIRED, result="nobody decided in time")
                out.append(self._save(new, "action_expired"))
        return out

    def approved(self) -> list[Action]:
        return [a for a in self.actions.values() if a.status == APPROVED]

    def finish(self, action_id: int, status: str, result: str, now: datetime) -> Action | None:
        a = self.actions.get(action_id)
        if a is None or a.status != APPROVED:
            return None
        return self._save(
            replace(a, status=status, result=result, executed_at=now), f"action_{status}"
        )

    def executed_since(self, since: datetime) -> int:
        return sum(
            1
            for a in self.actions.values()
            if a.status == "executed" and a.executed_at is not None and a.executed_at >= since
        )
