"""Command-line entry point.

centinela collect                       run the incident collector (in the cluster)
centinela incidents list                recent incidents with MTTD / MTTR
centinela incidents show ID             timeline: alerts, logs, resolution
centinela incidents close ID --cause .. --resolution ..   write the postmortem
centinela incidents stats               averages for the whole history
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC

from centinela import __version__
from centinela.config import Settings
from centinela.incidents.models import Incident
from centinela.incidents.store import PostgresIncidentStore, make_pool


def _fmt_seconds(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 120:
        return f"{value:.0f}s"
    return f"{value / 60:.1f}m"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="centinela",
        description="AI SRE agent that detects, diagnoses and safely fixes incidents.",
    )
    parser.add_argument("--version", action="version", version=f"centinela {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("collect", help="run the incident collector")

    incidents = sub.add_parser("incidents", help="incident history")
    isub = incidents.add_subparsers(dest="action", required=True)
    lst = isub.add_parser("list", help="recent incidents")
    lst.add_argument("--limit", type=int, default=20)
    show = isub.add_parser("show", help="incident timeline")
    show.add_argument("id", type=int)
    close = isub.add_parser("close", help="record root cause and resolution")
    close.add_argument("id", type=int)
    close.add_argument("--cause", required=True)
    close.add_argument("--resolution", required=True)
    isub.add_parser("stats", help="MTTD / MTTR averages")
    return parser


def _store() -> PostgresIncidentStore:
    return PostgresIncidentStore(make_pool(Settings.from_env().database_url))


def print_incidents(rows: list[Incident]) -> None:
    print(f"{'ID':>4}  {'STATUS':8}  {'SEV':8}  {'SERVICE':10}  {'MTTD':>6}  {'MTTR':>6}  TITLE")
    for i in rows:
        print(
            f"{i.id:>4}  {i.status:8}  {i.severity:8}  {(i.service or '-'):10}  "
            f"{_fmt_seconds(i.mttd_seconds):>6}  {_fmt_seconds(i.mttr_seconds):>6}  {i.title}"
        )


def run_collect() -> int:
    from prometheus_client import start_http_server

    from centinela.incidents.collector import Collector
    from centinela.incidents.sources import LokiLogs, PrometheusAlerts
    from centinela.logs import configure_logging

    configure_logging("incident-collector")
    settings = Settings.from_env()
    store = PostgresIncidentStore(make_pool(settings.database_url))
    store.migrate(settings.reader_password)
    start_http_server(settings.metrics_port)
    Collector(
        PrometheusAlerts(settings.prometheus_url), store, LokiLogs(settings.loki_url)
    ).run_forever(settings.poll_interval_s)
    return 0


def run_incidents(args: argparse.Namespace) -> int:
    store = _store()
    if args.action == "list":
        print_incidents(store.recent(args.limit))
    elif args.action == "show":
        incident = store.get(args.id)
        if incident is None:
            print(f"incident {args.id} not found", file=sys.stderr)
            return 1
        print_incidents([incident])
        print(f"\nroot cause: {incident.root_cause or '-'}")
        print(f"resolution: {incident.resolution or '-'}\n")
        print("timeline (UTC):")
        for event in store.events(args.id):
            payload = event["payload"]
            if event["kind"] == "logs":
                payload = {"service": payload.get("service"), "lines": len(payload["lines"])}
            at = event["at"].astimezone(UTC)
            print(f"{at:%H:%M:%S}  {event['kind']:18}  {json.dumps(payload)}")
    elif args.action == "close":
        if not store.close(args.id, args.cause, args.resolution):
            print(f"incident {args.id} not found", file=sys.stderr)
            return 1
        print(f"incident {args.id} updated")
    elif args.action == "stats":
        rows = store.recent(limit=10_000)
        resolved = [i for i in rows if i.mttr_seconds is not None]
        mttd = sum(i.mttd_seconds for i in rows) / len(rows) if rows else None
        mttr = sum(i.mttr_seconds or 0 for i in resolved) / len(resolved) if resolved else None
        print(f"incidents: {len(rows)}  open: {len(rows) - len(resolved)}")
        print(f"MTTD avg:  {_fmt_seconds(mttd)}")
        print(f"MTTR avg:  {_fmt_seconds(mttr)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "collect":
        return run_collect()
    if args.command == "incidents":
        return run_incidents(args)
    print(f"centinela {__version__} - phase 5: incident history (try: centinela --help)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
