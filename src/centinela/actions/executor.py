"""The executor: the ONLY component that can change the shop, and only after a human approved.

    centinela executor

It runs with its own ServiceAccount (`centinela-executor`), whose Role allows `get` and
`patch` on four named deployments (catalog, orders, payments, redis) and nothing else: no
create, no delete, no exec, no Secrets, no database. It has no network exposure. The web page
and the CLI can only *approve*; they cannot touch Kubernetes.

Before acting it re-checks everything, because time has passed since the approval:
policy (same code as the proposal), the hourly rate limit, and whether the action is still
needed. After acting it verifies the result and records it in the incident timeline.
"""

import logging
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import FrameType
from typing import Any, Protocol

import httpx
from prometheus_client import Counter

from centinela.actions.models import EXECUTED, FAILED, SKIPPED, Action
from centinela.actions.policy import MAX_EXECUTIONS_PER_HOUR, PolicyError, check
from centinela.actions.store import ActionStore
from centinela.agent.k8s import SA_DIR

log = logging.getLogger("centinela.executor")

ACTIONS = Counter("centinela_actions_total", "Actions by final status.", ["kind", "status"])


@dataclass(frozen=True)
class DeploymentState:
    name: str
    desired: int
    ready: int


class ShopWriter(Protocol):
    def state(self, name: str) -> DeploymentState: ...
    def scale(self, name: str, replicas: int) -> None: ...
    def restart(self, name: str, at: datetime) -> None: ...


class KubeShopWriter:
    """Minimal Kubernetes writes through the API server (merge patches only)."""

    def __init__(self, client: httpx.Client, namespace: str = "shop") -> None:
        self.http = client
        self.base = f"/apis/apps/v1/namespaces/{namespace}/deployments"

    @classmethod
    def in_cluster(cls, namespace: str = "shop") -> "KubeShopWriter | None":
        token = SA_DIR / "token"
        if not token.exists():
            return None
        client = httpx.Client(
            base_url="https://kubernetes.default.svc",
            verify=str(SA_DIR / "ca.crt"),
            headers={"Authorization": f"Bearer {token.read_text().strip()}"},
            timeout=10.0,
        )
        return cls(client, namespace)

    def _patch(self, path: str, body: dict[str, Any]) -> None:
        resp = self.http.patch(
            path, json=body, headers={"Content-Type": "application/merge-patch+json"}
        )
        resp.raise_for_status()

    def state(self, name: str) -> DeploymentState:
        resp = self.http.get(f"{self.base}/{name}")
        resp.raise_for_status()
        item = resp.json()
        return DeploymentState(
            name,
            int(item.get("spec", {}).get("replicas", 1)),
            int(item.get("status", {}).get("readyReplicas", 0)),
        )

    def scale(self, name: str, replicas: int) -> None:
        self._patch(f"{self.base}/{name}/scale", {"spec": {"replicas": replicas}})

    def restart(self, name: str, at: datetime) -> None:
        # Same thing `kubectl rollout restart` does: a new annotation -> new pods, one by one
        annotation = {"kubectl.kubernetes.io/restartedAt": at.isoformat()}
        self._patch(
            f"{self.base}/{name}",
            {"spec": {"template": {"metadata": {"annotations": annotation}}}},
        )


class Executor:
    def __init__(
        self,
        store: ActionStore,
        shop: ShopWriter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        verify_timeout_s: float = 90.0,
        sleep: Callable[[float], None] = time.sleep,
        dry_run: bool = False,
    ) -> None:
        self.store = store
        self.shop = shop
        self.clock = clock
        self.verify_timeout_s = verify_timeout_s
        self.sleep = sleep
        self.dry_run = dry_run

    def cycle(self) -> list[Action]:
        now = self.clock()
        for expired in self.store.expire(now):
            ACTIONS.labels(expired.kind, "expired").inc()
            log.info("action expired", extra={"fields": {"action_id": expired.id}})
        done: list[Action] = []
        for action in self.store.approved():
            finished = self.execute(action)
            if finished is not None:
                ACTIONS.labels(finished.kind, finished.status).inc()
                done.append(finished)
        return done

    def _finish(self, action: Action, status: str, result: str) -> Action | None:
        log.info(
            "action finished",
            extra={"fields": {"action_id": action.id, "status": status, "result": result}},
        )
        return self.store.finish(action.id, status, result, self.clock())

    def execute(self, action: Action) -> Action | None:
        try:
            check(action.kind, action.target, action.params)  # the rules may have changed
        except PolicyError as exc:
            return self._finish(action, FAILED, f"blocked by policy: {exc}")
        hour_ago = self.clock() - timedelta(hours=1)
        if self.store.executed_since(hour_ago) >= MAX_EXECUTIONS_PER_HOUR:
            return self._finish(
                action, FAILED, f"rate limit: {MAX_EXECUTIONS_PER_HOUR} actions in the last hour"
            )
        try:
            before = self.shop.state(action.target)
            if action.kind == "scale_up":
                replicas = int(action.params.get("replicas", 1))
                if before.desired >= replicas and before.ready >= replicas:
                    return self._finish(
                        action,
                        SKIPPED,
                        f"not needed: {action.target} already has {before.ready}/{before.desired}",
                    )
                if self.dry_run:
                    return self._finish(action, SKIPPED, f"dry run: would scale to {replicas}")
                self.shop.scale(action.target, replicas)
            elif action.kind == "restart":
                if self.dry_run:
                    return self._finish(action, SKIPPED, "dry run: would restart")
                self.shop.restart(action.target, self.clock())
            return self._verify(action, before)
        except httpx.HTTPError as exc:
            return self._finish(action, FAILED, f"kubernetes error: {exc!r}"[:300])

    def _verify(self, action: Action, before: DeploymentState) -> Action | None:
        """Wait until the deployment has its desired replicas ready again."""
        started = time.monotonic()
        state = before
        while time.monotonic() - started < self.verify_timeout_s:
            self.sleep(3)
            state = self.shop.state(action.target)
            if state.desired >= 1 and state.ready >= state.desired:
                took = time.monotonic() - started
                return self._finish(
                    action,
                    EXECUTED,
                    f"{action.describe()}: {before.ready}/{before.desired} -> "
                    f"{state.ready}/{state.desired} ready after {took:.0f} s",
                )
        return self._finish(
            action,
            FAILED,
            f"{action.describe()} applied but only {state.ready}/{state.desired} ready "
            f"after {self.verify_timeout_s:.0f} s",
        )

    def run_forever(self, interval_s: float) -> None:
        stop = {"now": False}

        def handle_stop(signum: int, frame: FrameType | None) -> None:
            stop["now"] = True

        signal.signal(signal.SIGTERM, handle_stop)
        log.info("executor started", extra={"fields": {"dry_run": self.dry_run}})
        while not stop["now"]:
            started = time.monotonic()
            try:
                self.cycle()
            except Exception:
                log.exception("executor cycle failed")
            time.sleep(max(0.0, interval_s - (time.monotonic() - started)))
        log.info("executor stopped")
