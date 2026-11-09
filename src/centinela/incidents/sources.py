"""Read-only clients for the observability stack."""

import json
from datetime import datetime
from typing import Any

import httpx

from centinela.incidents.models import Alert


class PrometheusAlerts:
    """Firing alerts from Prometheus (`/api/v1/alerts`). Pending alerts are ignored."""

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self.http = client or httpx.Client(base_url=base_url, timeout=5.0)

    def firing(self) -> list[Alert]:
        resp = self.http.get("/api/v1/alerts")
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            raise RuntimeError(f"prometheus error: {body.get('error', body)}")
        return [
            Alert.from_prometheus(raw)
            for raw in body["data"]["alerts"]
            if raw.get("state") == "firing"
        ]


def logql_for(service: str | None) -> str:
    """Warnings and errors of one service (or of all of them)."""
    selector = 'project="centinela", level=~"error|warning"'
    if service:
        selector += f', service="{service}"'
    return "{" + selector + "}"


class LokiLogs:
    """Recent error/warning lines, attached to incidents as evidence."""

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self.http = client or httpx.Client(base_url=base_url, timeout=5.0)

    def recent(
        self, service: str | None, start: datetime, end: datetime, limit: int = 20
    ) -> list[dict[str, Any]]:
        resp = self.http.get(
            "/loki/api/v1/query_range",
            params={
                "query": logql_for(service),
                "start": str(int(start.timestamp() * 1e9)),
                "end": str(int(end.timestamp() * 1e9)),
                "limit": str(limit),
                "direction": "backward",
            },
        )
        resp.raise_for_status()
        lines: list[dict[str, Any]] = []
        for stream in resp.json()["data"]["result"]:
            labels = stream.get("stream", {})
            for _ts, line in stream.get("values", []):
                try:
                    parsed: dict[str, Any] = json.loads(line)
                except ValueError:
                    parsed = {"msg": line}
                parsed.setdefault("service", labels.get("service"))
                lines.append(parsed)
        lines.sort(key=lambda entry: str(entry.get("ts", "")), reverse=True)
        return lines[:limit]


class PrometheusQuery:
    """Instant PromQL queries (`/api/v1/query`), used by the agent to gather evidence."""

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self.http = client or httpx.Client(base_url=base_url, timeout=10.0)

    def vector(self, query: str, at: datetime) -> list[tuple[dict[str, str], float]]:
        resp = self.http.get("/api/v1/query", params={"query": query, "time": at.timestamp()})
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            raise RuntimeError(f"prometheus error: {body.get('error', body)}")
        return [
            ({str(k): str(v) for k, v in r["metric"].items()}, float(r["value"][1]))
            for r in body["data"]["result"]
        ]
