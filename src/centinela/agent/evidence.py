"""Gather the evidence for one incident, as numbered items the model must cite.

Every item has an id ([E1], [E2], ...). The model may only cite ids that exist, and the
cited items must mention the service it blames: that is how we check it did not invent
anything. Text coming from the system under diagnosis (log lines, Kubernetes event messages)
is untrusted: it is shortened, flattened to one line and fenced, never treated as orders.
"""

import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from centinela.agent.k8s import KubeEvent, KubeReader, PodState, Workload
from centinela.incidents.models import Incident, IncidentAlert
from centinela.incidents.store import IncidentStore

log = logging.getLogger("centinela.evidence")

# Components the agent may blame. Everything in the shop that can break.
APP_SERVICES = ("catalog", "orders", "payments")
COMPONENTS = (*APP_SERVICES, "postgres", "redis")

ALERT_RUNBOOKS = {
    "ServiceDown": "service-down.md",
    "HighErrorRate": "high-error-rate.md",
    "UpstreamErrors": "upstream-errors.md",
    "HighLatencyP95": "high-latency.md",
    "CacheDegraded": "cache-degraded.md",
    "HighMemoryUsage": "high-memory.md",
}

# Metrics are read shortly after detection: the moment the incident was visible.
METRICS_AFTER_DETECTION = timedelta(seconds=60)
RUNBOOK_CHARS = 900
UNTRUSTED_CHARS = 160


class Metrics(Protocol):
    def vector(self, query: str, at: datetime) -> list[tuple[dict[str, str], float]]: ...


QUERIES = {
    "up": 'max by (service) (up{job="shop"})',
    "rps": 'sum by (service) (rate(http_requests_total{job="shop"}[2m]))',
    "errors": (
        'sum by (service) (rate(http_requests_total{job="shop", status=~"5.."}[2m]))'
        ' / sum by (service) (rate(http_requests_total{job="shop"}[2m]))'
    ),
    "p95": (
        "histogram_quantile(0.95, sum by (service, le) "
        '(rate(http_request_duration_seconds_bucket{job="shop"}[5m])))'
    ),
    "mem": 'max by (service) (process_resident_memory_bytes{job="shop"})',
}
UPSTREAM_QUERY = 'sum by (upstream) (rate(shop_upstream_errors_total{job="shop"}[2m]))'
CACHE_QUERY = 'sum by (result) (rate(shop_cache_requests_total{job="shop"}[2m]))'


def alert_components(alert: IncidentAlert) -> set[str]:
    """Components an alert is about. UpstreamErrors is raised BY orders (the caller whose
    requests fail) ABOUT its upstream: both are involved, orders as the symptom."""
    components = {alert.service} if alert.service else set()
    if alert.name == "UpstreamErrors":
        components.add("orders")
    return components


@dataclass(frozen=True)
class Item:
    id: str
    # alert | metrics | dependencies | cache | workloads | pods | k8s_events | logs | runbook
    # | history
    kind: str
    title: str
    body: str
    services: frozenset[str] = frozenset()

    def mentions(self, service: str) -> bool:
        return (
            service in self.services
            or re.search(rf"\b{re.escape(service)}\b", self.body) is not None
        )


@dataclass
class Evidence:
    incident: Incident
    alerts: list[IncidentAlert]
    observed_at: datetime
    items: list[Item] = field(default_factory=list)
    workloads: list[Workload] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, kind: str, title: str, body: str, services: set[str] | None = None) -> None:
        self.items.append(
            Item(f"E{len(self.items) + 1}", kind, title, body, frozenset(services or set()))
        )

    def ids(self) -> set[str]:
        return {i.id for i in self.items}

    def by_id(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    def render(self) -> str:
        inc = self.incident
        header = [
            f'INCIDENT #{inc.id}: "{inc.title}" (first alert {inc.category}, severity '
            f"{inc.severity}, status {inc.status})",
            f"started {_hms(inc.started_at)} UTC, detected {_hms(inc.detected_at)} UTC"
            + (f", resolved {_hms(inc.resolved_at)} UTC" if inc.resolved_at else ""),
            f"metrics observed at {_hms(self.observed_at)} UTC",
        ]
        header += [f"note: {n}" for n in self.notes]
        blocks = [f"[{i.id}] {i.kind}: {i.title}\n{i.body}" for i in self.items]
        return "\n".join(header) + "\n\n" + "\n\n".join(blocks)


def _hms(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%H:%M:%S")


def untrusted(text: object, limit: int = UNTRUSTED_CHARS) -> str:
    """One line, no control characters, bounded length, cannot fake an evidence id."""
    flat = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text))
    flat = flat.replace("[E", "[e").replace("```", "'''")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _fmt_rate(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _fmt_pct(value: float | None) -> str:
    return "-" if value is None or value != value else f"{value * 100:.0f}%"  # NaN-safe


def _fmt_secs(value: float | None) -> str:
    if value is None or value != value:
        return "-"
    return f"{value * 1000:.0f}ms" if value < 1 else f"{value:.1f}s"


def _fmt_mib(value: float | None) -> str:
    return "-" if value is None else f"{value / 2**20:.0f}MiB"


class EvidenceBuilder:
    def __init__(
        self,
        store: IncidentStore,
        metrics: Metrics | None,
        kube: KubeReader | None,
        runbooks_dir: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.metrics = metrics
        self.kube = kube
        self.runbooks_dir = runbooks_dir
        self.clock = clock

    def build(self, incident: Incident) -> Evidence:
        now = self.clock()
        observed = incident.detected_at + METRICS_AFTER_DETECTION
        if incident.resolved_at is not None:
            observed = min(observed, incident.resolved_at)
        observed = min(observed, now)
        ev = Evidence(incident, self.store.alerts(incident.id), observed)

        self._alerts(ev)
        self._best_effort(ev, "metrics", lambda: self._metrics(ev))
        self._best_effort(ev, "kubernetes", lambda: self._kubernetes(ev, now))
        self._logs(ev)
        self._runbooks(ev)
        self._history(ev)
        return ev

    @staticmethod
    def _best_effort(ev: Evidence, what: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:  # evidence is partial rather than missing
            log.warning(
                "evidence unavailable", extra={"fields": {"source": what, "error": repr(exc)}}
            )
            ev.notes.append(f"{what} unavailable ({type(exc).__name__})")

    # -- alerts ---------------------------------------------------------------------------
    def _alerts(self, ev: Evidence) -> None:
        for a in ev.alerts:
            labels = ", ".join(
                f"{k}={v}"
                for k, v in sorted(a.labels.items())
                if k not in {"alertname", "severity"}
            )
            state = f"cleared {_hms(a.resolved_at)}" if a.resolved_at else "still firing"
            ev.add(
                "alert",
                f"{a.name} ({a.severity}) {labels}",
                f"{a.summary}. Active since {_hms(a.active_at)} UTC, {state}.",
                alert_components(a),
            )

    # -- metrics --------------------------------------------------------------------------
    def _metrics(self, ev: Evidence) -> None:
        if self.metrics is None:
            ev.notes.append("metrics unavailable (no Prometheus configured)")
            return
        at = ev.observed_at
        if not self.metrics.vector(QUERIES["up"], at):
            # Not "everything is down": Prometheus simply has no samples for that moment
            # (its storage is an emptyDir, wiped when the pod restarts).
            ev.notes.append(
                f"metrics: Prometheus has no data for {_hms(at)} UTC (history lost on restart)"
            )
            return
        table: dict[str, dict[str, float]] = {s: {} for s in APP_SERVICES}
        for name, query in QUERIES.items():
            for labels, value in self.metrics.vector(query, at):
                service = labels.get("service")
                if service:
                    table.setdefault(service, {})[name] = value
        lines = [f"{'service':<10}{'up':>4}{'req/s':>8}{'5xx':>6}{'p95':>8}{'memory':>9}"]
        for service, row in sorted(table.items()):
            if "up" not in row:
                lines.append(f"{service:<10}  NO SCRAPE TARGET (no running instance found)")
                continue
            lines.append(
                f"{service:<10}{row['up']:>4.0f}{_fmt_rate(row.get('rps')):>8}"
                f"{_fmt_pct(row.get('errors')):>6}{_fmt_secs(row.get('p95')):>8}"
                f"{_fmt_mib(row.get('mem')):>9}"
            )
        ev.add("metrics", "per service (Prometheus)", "\n".join(lines), set(table))

        upstream = {
            lbl.get("upstream", "?"): v for lbl, v in self.metrics.vector(UPSTREAM_QUERY, at)
        }
        failing = {k: v for k, v in upstream.items() if v > 0}
        body = (
            ", ".join(f"orders -> {k}: {v:.2f} failed calls/s" for k, v in sorted(failing.items()))
            if failing
            else "orders -> catalog/payments: no failed calls"
        )
        ev.add("dependencies", "calls from orders to its dependencies", body, set(failing))

        cache = {lbl.get("result", "?"): v for lbl, v in self.metrics.vector(CACHE_QUERY, at)}
        if cache.get("error", 0) > 0:
            ev.add(
                "cache",
                "catalog cache (Redis) lookups",
                f"errors {cache['error']:.2f}/s, hits {cache.get('hit', 0):.2f}/s",
                {"redis", "catalog"},
            )

    # -- kubernetes -----------------------------------------------------------------------
    def _kubernetes(self, ev: Evidence, now: datetime) -> None:
        if self.kube is None:
            ev.notes.append("kubernetes state unavailable (agent not running in the cluster)")
            return
        when = "now" if ev.incident.status == "open" else "now, AFTER the incident: may differ"
        ev.workloads = self.kube.workloads()
        ev.add(
            "workloads",
            f"shop deployments and statefulsets, ready/desired replicas ({when})",
            "\n".join(_workload_line(w) for w in ev.workloads),
            {w.name for w in ev.workloads if w.ready < w.desired or w.desired == 0},
        )
        pods = self.kube.pods()
        unhealthy = [p for p in pods if not p.ready or p.restarts or p.last_termination]
        if unhealthy:
            ev.add(
                "pods",
                f"pods not ready or restarted ({when})",
                "\n".join(_pod_line(p) for p in unhealthy),
                {p.app for p in unhealthy},
            )
        events = self.kube.warning_events(since=ev.incident.started_at - timedelta(minutes=5))
        if events:
            ev.add(
                "k8s_events",
                "Kubernetes warning events (untrusted text)",
                "\n".join(_event_line(e) for e in events),
                {s for s in COMPONENTS for e in events if e.object.startswith(s)},
            )

    # -- logs -----------------------------------------------------------------------------
    def _logs(self, ev: Evidence) -> None:
        """Error/warning logs attached by the collector, grouped: same message -> one line."""
        per_service: dict[str, Counter[str]] = {}
        for event in self.store.events(ev.incident.id):
            if event["kind"] != "logs":
                continue
            payload: dict[str, Any] = event["payload"]
            for line in payload.get("lines", []):
                service = str(line.get("service") or payload.get("service") or "?")
                extra = f" upstream={line['upstream']}" if line.get("upstream") else ""
                key = f'{line.get("level", "?")} "{untrusted(line.get("msg", ""))}"{extra}'
                per_service.setdefault(service, Counter())[key] += 1
        for service, counts in sorted(per_service.items()):
            body = "\n".join(f"{n}x {msg}" for msg, n in counts.most_common(5))
            ev.add("logs", f"{service} error/warning log lines (untrusted text)", body, {service})

    # -- runbooks -------------------------------------------------------------------------
    def _runbooks(self, ev: Evidence) -> None:
        for name in dict.fromkeys(a.name for a in ev.alerts):  # unique, in order
            file = ALERT_RUNBOOKS.get(name)
            if file is None:
                continue
            path = self.runbooks_dir / file
            if not path.exists():
                ev.notes.append(f"runbook {file} not found")
                continue
            ev.add("runbook", f"{file} (for {name})", _runbook_excerpt(path.read_text()))

    # -- history --------------------------------------------------------------------------
    def _history(self, ev: Evidence) -> None:
        for past in self.store.similar(ev.incident):
            ev.add(
                "history",
                f"past incident #{past.id}: {past.title} ({past.category})",
                f"root cause: {untrusted(past.root_cause, 200)}\n"
                f"resolution: {untrusted(past.resolution, 200)}",
                {past.service} if past.service else set(),
            )


def _workload_line(w: Workload) -> str:
    flag = (
        "  <- SCALED TO ZERO"
        if w.desired == 0
        else ("  <- NOT READY" if w.ready < w.desired else "")
    )
    return f"{w.kind} {w.name}: {w.ready}/{w.desired}{flag}"


def _pod_line(p: PodState) -> str:
    last = f", last termination {p.last_termination}" if p.last_termination else ""
    return f"{p.name} ({p.app}): {p.phase}, ready={p.ready}, restarts={p.restarts}{last}"


def _event_line(e: KubeEvent) -> str:
    return f"{e.reason} {e.object} x{e.count}: {untrusted(e.message)}"


def _runbook_excerpt(text: str) -> str:
    """'What it means' and 'Fix' sections only: the parts that help pick a cause and action."""
    keep: list[str] = []
    for section in re.split(r"\n(?=## )", text):
        title = section.splitlines()[0].lower() if section else ""
        if title.startswith("## what it means") or title.startswith("## fix"):
            keep.append(section.strip())
    excerpt = re.sub(r"\n{2,}", "\n", "\n".join(keep)) or text
    return excerpt[:RUNBOOK_CHARS]
