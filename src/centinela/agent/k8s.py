"""Read-only view of the shop in Kubernetes, through the API server.

Uses the pod's own ServiceAccount (`centinela-agent`), which Terraform binds to a Role that
can only get/list pods, events and deployments in the `shop` namespace and has no access to
Secrets. Plain HTTPS with httpx: no Kubernetes client library needed.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


@dataclass(frozen=True)
class Workload:
    kind: str
    name: str
    desired: int
    ready: int


@dataclass(frozen=True)
class PodState:
    name: str
    app: str
    phase: str
    ready: bool
    restarts: int
    last_termination: str | None  # e.g. "OOMKilled", "Error"


@dataclass(frozen=True)
class KubeEvent:
    reason: str
    object: str
    message: str
    count: int
    last_seen: datetime | None


class KubeReader:
    def __init__(self, client: httpx.Client, namespace: str = "shop") -> None:
        self.http = client
        self.ns = namespace

    @classmethod
    def in_cluster(cls, namespace: str = "shop") -> "KubeReader | None":
        """None when not running inside Kubernetes (e.g. tests, a laptop shell)."""
        token_file = SA_DIR / "token"
        if not token_file.exists():
            return None
        client = httpx.Client(
            base_url="https://kubernetes.default.svc",
            verify=str(SA_DIR / "ca.crt"),
            headers={"Authorization": f"Bearer {token_file.read_text().strip()}"},
            timeout=5.0,
        )
        return cls(client, namespace)

    def _get(self, path: str) -> list[dict[str, Any]]:
        resp = self.http.get(path)
        resp.raise_for_status()
        items: list[dict[str, Any]] = resp.json().get("items", [])
        return items

    def workloads(self) -> list[Workload]:
        out: list[Workload] = []
        for kind, path in (
            ("deployment", f"/apis/apps/v1/namespaces/{self.ns}/deployments"),
            ("statefulset", f"/apis/apps/v1/namespaces/{self.ns}/statefulsets"),
        ):
            for item in self._get(path):
                out.append(
                    Workload(
                        kind,
                        item["metadata"]["name"],
                        int(item.get("spec", {}).get("replicas", 1)),
                        int(item.get("status", {}).get("readyReplicas", 0)),
                    )
                )
        return sorted(out, key=lambda w: w.name)

    def pods(self) -> list[PodState]:
        out: list[PodState] = []
        for item in self._get(f"/api/v1/namespaces/{self.ns}/pods"):
            meta, status = item["metadata"], item.get("status", {})
            containers = status.get("containerStatuses", [])
            last = None
            for c in containers:
                terminated = c.get("lastState", {}).get("terminated")
                if terminated:
                    last = str(terminated.get("reason", "Terminated"))
            out.append(
                PodState(
                    name=meta["name"],
                    app=meta.get("labels", {}).get("app.kubernetes.io/name", "?"),
                    phase=str(status.get("phase", "Unknown")),
                    ready=bool(containers) and all(c.get("ready") for c in containers),
                    restarts=sum(int(c.get("restartCount", 0)) for c in containers),
                    last_termination=last,
                )
            )
        return sorted(out, key=lambda p: p.name)

    def warning_events(self, since: datetime, limit: int = 10) -> list[KubeEvent]:
        out: list[KubeEvent] = []
        for item in self._get(f"/api/v1/namespaces/{self.ns}/events"):
            if item.get("type") != "Warning":
                continue
            stamp = item.get("lastTimestamp") or item.get("eventTime")
            seen = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None
            if seen is not None and seen < since:
                continue
            out.append(
                KubeEvent(
                    reason=str(item.get("reason", "")),
                    object=str(item.get("involvedObject", {}).get("name", "")),
                    message=str(item.get("message", "")),
                    count=int(item.get("count") or 1),
                    last_seen=seen,
                )
            )
        out.sort(key=lambda e: e.last_seen.timestamp() if e.last_seen else 0.0, reverse=True)
        return out[:limit]
