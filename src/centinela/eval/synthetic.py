"""Synthetic evaluation cases: incidents that are hard or slow to cause in a laptop cluster.

The chaos run (`scripts/chaos.sh`) records real incidents. These add what it cannot produce
safely or quickly: two faults at once, a slow database, a crash loop, a pod killed for memory,
a noisy-but-harmless alert, a rollout that is a red herring... Each one is built with the real
EvidenceBuilder from a small description of what Prometheus and Kubernetes would show, so the
model receives exactly the kind of prompt it gets in production.

    uv run centinela eval synthesize        (writes eval/cases/synthetic-*.json)
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from centinela.agent import snapshot
from centinela.agent.evidence import EvidenceBuilder
from centinela.agent.guard import InjectionDetector
from centinela.agent.k8s import KubeEvent, KubeReader, PodState, Workload
from centinela.eval.cases import Case
from centinela.incidents.models import Alert, fingerprint
from centinela.incidents.store import InMemoryIncidentStore

T0 = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
DETECTED = T0 + timedelta(seconds=75)
NOW = T0 + timedelta(minutes=3)
APPS = ("catalog", "orders", "payments")
SUMMARIES = {
    "ServiceDown": "{s} is down",
    "HighErrorRate": "{s} returns too many 5xx errors",
    "HighLatencyP95": "{s} is slow",
    "UpstreamErrors": "orders cannot reach {s}",
    "CacheDegraded": "catalog cache (Redis) is failing",
    "HighMemoryUsage": "{s} uses too much memory",
}
SEVERITY = {"ServiceDown": "critical", "HighErrorRate": "critical"}


@dataclass(frozen=True)
class Svc:
    up: bool = True
    rps: float = 1.5
    errors: float = 0.0  # ratio of 5xx
    p95: float = 0.04  # seconds
    mem_mib: float = 75


@dataclass(frozen=True)
class Spec:
    name: str
    fault: str
    alerts: tuple[tuple[str, str, int], ...]  # (alert, service or upstream, seconds after T0)
    root_cause: tuple[str, ...]
    category: tuple[str, ...]
    actions: tuple[str, ...]
    services: dict[str, Svc] = field(default_factory=dict)
    upstream_errors: dict[str, float] = field(default_factory=dict)
    cache_errors: float = 0.0
    workloads: dict[str, tuple[int, int]] = field(default_factory=dict)  # name: (desired, ready)
    rollouts: dict[str, int] = field(default_factory=dict)  # name: minutes before T0
    pods: tuple[PodState, ...] = ()
    events: tuple[tuple[str, str, str], ...] = ()  # (reason, object, message)
    logs: dict[str, tuple[tuple[str, str, int], ...]] = field(default_factory=dict)
    history: tuple[tuple[str, str, str, str], ...] = ()  # (alert, service, cause, fix)


class _Metrics:
    def __init__(self, spec: Spec) -> None:
        self.spec = spec

    def vector(self, query: str, at: datetime) -> list[tuple[dict[str, str], float]]:
        svcs = {a: self.spec.services.get(a, Svc()) for a in APPS}
        live = {a: s for a, s in svcs.items() if s.up}
        if query.startswith("max by (service) (up"):
            return [({"service": a}, 1.0) for a in live]
        if "status=~" in query:
            return [({"service": a}, s.errors) for a, s in live.items()]
        if "histogram_quantile" in query:
            return [({"service": a}, s.p95) for a, s in live.items()]
        if "process_resident_memory_bytes" in query:
            return [({"service": a}, s.mem_mib * 2**20) for a, s in live.items()]
        if "shop_upstream_errors_total" in query:
            return [({"upstream": u}, v) for u, v in self.spec.upstream_errors.items()]
        if "shop_cache_requests_total" in query:
            out = [({"result": "hit"}, 2.5), ({"result": "miss"}, 0.4)]
            return out + (
                [({"result": "error"}, self.spec.cache_errors)] if self.spec.cache_errors else []
            )
        if "http_requests_total" in query:
            return [({"service": a}, s.rps) for a, s in live.items()]
        return []


class _Kube(KubeReader):
    def __init__(self, spec: Spec) -> None:  # no API server
        self.ns = "shop"
        self.spec = spec

    def workloads(self) -> list[Workload]:
        names = {
            "catalog": "deployment",
            "orders": "deployment",
            "payments": "deployment",
            "postgres": "statefulset",
            "redis": "deployment",
        }
        out = []
        for name, kind in sorted(names.items()):
            desired, ready = self.spec.workloads.get(name, (1, 1))
            minutes = self.spec.rollouts.get(name)
            rollout = (
                T0 - timedelta(minutes=minutes) if minutes is not None else T0 - timedelta(days=1)
            )
            out.append(
                Workload(
                    kind,
                    name,
                    desired,
                    ready,
                    rollout if kind == "deployment" else None,
                    str(5 if minutes is not None else 4),
                )
            )
        return out

    def pods(self) -> list[PodState]:
        return list(self.spec.pods)

    def warning_events(self, since: datetime, limit: int = 10) -> list[KubeEvent]:
        return [KubeEvent(r, o, m, 3, DETECTED) for r, o, m in self.spec.events][:limit]


def build(spec: Spec, runbooks: Path) -> Case:
    store = InMemoryIncidentStore()
    alerts = []
    for name, target, offset in spec.alerts:
        key = "upstream" if name == "UpstreamErrors" else "service"
        labels = {"alertname": name, "severity": SEVERITY.get(name, "warning"), "job": "shop"}
        if name != "CacheDegraded":
            labels[key] = target
        summary = SUMMARIES[name].format(s=target)
        alerts.append(
            Alert(
                fingerprint(labels),
                name,
                labels["severity"],
                labels,
                summary,
                T0 + timedelta(seconds=offset),
            )
        )
    store.create(tuple(alerts), DETECTED)
    for service, lines in spec.logs.items():
        rows: list[dict[str, Any]] = []
        for level, msg, count in lines:
            rows += [{"level": level, "msg": msg}] * count
        store.add_event(1, "logs", {"service": service, "lines": rows}, DETECTED)
    for n, (alert, service, cause, fix) in enumerate(spec.history, start=2):
        past = Alert(
            fingerprint({"a": alert, "n": str(n)}),
            alert,
            SEVERITY.get(alert, "warning"),
            {"alertname": alert, "service": service},
            SUMMARIES[alert].format(s=service),
            T0 - timedelta(days=n),
        )
        store.create((past,), past.active_at + timedelta(seconds=70))
        store.resolve_incident(n, past.active_at + timedelta(minutes=5))
        store.close(n, cause, fix)
    incident = store.get(1)
    assert incident is not None  # noqa: S101 - just created
    builder = EvidenceBuilder(
        store,
        _Metrics(spec),
        _Kube(spec),
        runbooks,
        clock=lambda: NOW,
        detector=InjectionDetector(),
    )
    evidence = builder.build(incident)
    return Case(
        spec.name,
        "synthetic",
        spec.fault,
        spec.root_cause,
        spec.category,
        spec.actions,
        snapshot.to_json(evidence),
    )


def _pod(
    app: str, ready: bool, restarts: int, last: str | None, phase: str = "Running"
) -> PodState:
    return PodState(f"{app}-7c9d5f-k2x8q", app, phase, ready, restarts, last)


SPECS: tuple[Spec, ...] = (
    Spec(
        "synthetic-payments-crashloop",
        "payments crashes on start (bad configuration) and Kubernetes keeps restarting it",
        (
            ("ServiceDown", "payments", 0),
            ("UpstreamErrors", "payments", 15),
            ("HighErrorRate", "orders", 40),
        ),
        ("payments",),
        ("service_down",),
        ("none",),  # a restart is what Kubernetes already does
        services={"payments": Svc(up=False), "orders": Svc(errors=0.6)},
        upstream_errors={"payments": 1.4},
        workloads={"payments": (1, 0)},
        rollouts={"payments": 6},
        pods=(_pod("payments", False, 6, "Error", "CrashLoopBackOff"),),
        events=(
            ("BackOff", "payments-7c9d5f-k2x8q", "Back-off restarting failed container payments"),
        ),
        logs={"orders": (("error", "payments unavailable", 8),)},
    ),
    Spec(
        "synthetic-catalog-oomkilled",
        "catalog is killed for exceeding its memory limit, again and again",
        (("HighMemoryUsage", "catalog", 0), ("ServiceDown", "catalog", 50)),
        ("catalog",),
        ("resource_exhaustion",),
        ("none",),  # it would be killed again: raise the limit
        services={"catalog": Svc(up=False)},
        workloads={"catalog": (1, 0)},
        pods=(_pod("catalog", False, 3, "OOMKilled"),),
        logs={"orders": (("error", "catalog unavailable", 5),)},
    ),
    Spec(
        "synthetic-orders-memory-high",
        "orders memory grows slowly (leak), no errors yet",
        (("HighMemoryUsage", "orders", 0),),
        ("orders",),
        ("resource_exhaustion",),
        ("restart:orders", "none"),
        services={"orders": Svc(mem_mib=118)},
    ),
    Spec(
        "synthetic-slow-database",
        "PostgreSQL is overloaded: every service that queries it is slow, none is down",
        (
            ("HighLatencyP95", "catalog", 0),
            ("HighLatencyP95", "payments", 20),
            ("HighLatencyP95", "orders", 35),
        ),
        ("postgres",),
        ("high_latency", "dependency_failure"),
        ("none",),
        services={"catalog": Svc(p95=1.6), "payments": Svc(p95=1.9), "orders": Svc(p95=2.8)},
        logs={
            "catalog": (("warning", "slow query (1.4 s): SELECT * FROM products", 6),),
            "payments": (("warning", "slow query (1.7 s): INSERT INTO payments", 4),),
        },
    ),
    Spec(
        "synthetic-postgres-auth-failure",
        "the database password was rotated but the services still use the old one",
        (
            ("HighErrorRate", "catalog", 0),
            ("HighErrorRate", "payments", 10),
            ("HighErrorRate", "orders", 25),
        ),
        ("postgres",),
        ("dependency_failure", "error_spike"),
        ("none",),
        services={
            "catalog": Svc(errors=0.95),
            "payments": Svc(errors=0.97),
            "orders": Svc(errors=0.9),
        },
        logs={
            "catalog": (("error", "FATAL: password authentication failed for user shop", 9),),
            "payments": (("error", "FATAL: password authentication failed for user shop", 7),),
        },
    ),
    Spec(
        "synthetic-redis-errors",
        "redis is up but refuses commands (out of memory), catalog falls back to the database",
        (("CacheDegraded", "redis", 0),),
        ("redis",),
        ("cache_failure",),
        ("restart:redis", "none"),
        cache_errors=2.1,
        services={"catalog": Svc(p95=0.3)},
        logs={
            "catalog": (
                ("warning", "cache error: OOM command not allowed when used memory > maxmemory", 7),
            )
        },
    ),
    Spec(
        "synthetic-two-faults",
        "payments scaled to 0 AND redis failing at the same time",
        (
            ("ServiceDown", "payments", 0),
            ("CacheDegraded", "redis", 20),
            ("UpstreamErrors", "payments", 25),
        ),
        ("payments", "redis"),
        ("service_down", "cache_failure"),
        ("scale_up:payments",),
        services={"payments": Svc(up=False), "orders": Svc(errors=0.5)},
        cache_errors=1.5,
        upstream_errors={"payments": 1.2},
        workloads={"payments": (0, 0)},
        logs={
            "orders": (("error", "payments unavailable", 6),),
            "catalog": (("warning", "cache error: connection refused", 4),),
        },
    ),
    Spec(
        "synthetic-red-herring-rollout",
        "payments scaled to 0; catalog had an unrelated, healthy rollout 5 minutes before",
        (("ServiceDown", "payments", 0), ("UpstreamErrors", "payments", 15)),
        ("payments",),
        ("service_down",),
        ("scale_up:payments",),
        services={"payments": Svc(up=False), "orders": Svc(errors=0.4)},
        upstream_errors={"payments": 1.1},
        workloads={"payments": (0, 0)},
        rollouts={"catalog": 5},
        logs={"orders": (("error", "payments unavailable", 6),)},
    ),
    Spec(
        "synthetic-bad-release-errors",
        "a new catalog release returns 500 on 40% of requests",
        (("HighErrorRate", "catalog", 0),),
        ("catalog",),
        ("error_spike",),
        ("none",),
        services={"catalog": Svc(errors=0.4)},
        rollouts={"catalog": 3},
        logs={"catalog": (("error", "unhandled error while processing request", 9),)},
    ),
    Spec(
        "synthetic-orders-partial-outage",
        "one of two orders replicas is stuck not ready; the other serves alone and is slow",
        (("HighLatencyP95", "orders", 0),),
        ("orders",),
        ("high_latency", "resource_exhaustion"),
        ("restart:orders", "none"),
        services={"orders": Svc(p95=1.3, rps=2.4)},
        workloads={"orders": (2, 1)},
        pods=(_pod("orders", False, 0, None),),
    ),
    Spec(
        "synthetic-orders-down-cascade",
        "orders scaled to 0: nothing calls catalog or payments any more, their traffic drops",
        (("ServiceDown", "orders", 0),),
        ("orders",),
        ("service_down",),
        ("scale_up:orders",),
        services={"orders": Svc(up=False), "catalog": Svc(rps=0.3), "payments": Svc(rps=0.0)},
        workloads={"orders": (0, 0)},
    ),
    Spec(
        "synthetic-payments-errors-not-down",
        "payments answers but rejects most charges with 500 (its card provider is failing)",
        (("UpstreamErrors", "payments", 0), ("HighErrorRate", "payments", 20)),
        ("payments",),
        ("error_spike", "dependency_failure"),
        ("none",),  # the provider is broken
        services={"payments": Svc(errors=0.7), "orders": Svc(errors=0.3)},
        upstream_errors={"payments": 0.9},
        logs={
            "payments": (("error", "card provider timeout after 2.0s", 8),),
            "orders": (("error", "payments returned 500", 6),),
        },
    ),
    Spec(
        "synthetic-history-says-redis",
        "catalog scaled to 0; the most similar past incident was caused by redis",
        (("ServiceDown", "catalog", 0),),
        ("catalog",),
        ("service_down",),
        ("scale_up:catalog",),
        services={"catalog": Svc(up=False), "orders": Svc(errors=0.2)},
        workloads={"catalog": (0, 0)},
        history=(
            (
                "ServiceDown",
                "catalog",
                "redis evicted the product cache and catalog stopped answering",
                "restarted redis",
            ),
        ),
        logs={"orders": (("error", "catalog unavailable", 4),)},
    ),
    Spec(
        "synthetic-brief-blip",
        "a short burst of errors on orders that already stopped (no fault any more)",
        (("HighErrorRate", "orders", 0),),
        ("orders", "unknown"),
        ("error_spike", "unknown"),
        ("none",),
        services={"orders": Svc(errors=0.0)},
        logs={"orders": (("error", "request timed out", 3),)},
    ),
)


def write_all(runbooks: Path, out_dir: Path) -> list[Path]:
    import json

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for spec in SPECS:
        case = build(spec, runbooks)
        path = out_dir / f"{spec.name}.json"
        path.write_text(json.dumps(case.to_json(), indent=2, ensure_ascii=False) + "\n")
        paths.append(path)
    return paths
