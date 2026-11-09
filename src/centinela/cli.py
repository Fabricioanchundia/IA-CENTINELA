"""Command-line entry point.

centinela collect                       run the incident collector (in the cluster)
centinela agent                         diagnose new incidents automatically (in the cluster)
centinela diagnose ID [--dry-run]       diagnose one incident now (--dry-run: show the prompt)
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
from typing import TYPE_CHECKING, Any

from centinela import __version__
from centinela.config import Settings
from centinela.incidents.models import Incident
from centinela.incidents.store import PostgresIncidentStore, make_pool

if TYPE_CHECKING:
    from centinela.agent.diagnosis import Diagnoser


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
    sub.add_parser("agent", help="diagnose new incidents automatically")
    diag = sub.add_parser("diagnose", help="diagnose one incident with the local model")
    diag.add_argument("id", type=int)
    diag.add_argument("--dry-run", action="store_true", help="print the evidence, no model call")
    diag.add_argument("--no-store", action="store_true", help="do not save the result")

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


def _diagnoser(settings: Settings) -> "Diagnoser":
    from pathlib import Path

    from centinela.agent.diagnosis import Diagnoser
    from centinela.agent.evidence import EvidenceBuilder
    from centinela.agent.k8s import KubeReader
    from centinela.agent.llm import OllamaChat
    from centinela.incidents.sources import PrometheusQuery

    store = PostgresIncidentStore(make_pool(settings.database_url))
    builder = EvidenceBuilder(
        store,
        PrometheusQuery(settings.prometheus_url),
        KubeReader.in_cluster(settings.shop_namespace),
        Path(settings.runbooks_dir),
    )
    llm = OllamaChat(
        settings.ollama_url,
        settings.ollama_model,
        num_ctx=settings.ollama_num_ctx,
        think=settings.ollama_think,
    )
    # ~3 characters per token, keep room for the schema and a ~600-token answer
    return Diagnoser(store, builder, llm, max_prompt_chars=(settings.ollama_num_ctx - 900) * 3)


def run_agent() -> int:
    from datetime import timedelta

    from prometheus_client import start_http_server

    from centinela.agent.runner import AgentLoop
    from centinela.logs import configure_logging

    configure_logging("centinela-agent")
    settings = Settings.from_env()
    diagnoser = _diagnoser(settings)
    start_http_server(settings.metrics_port)
    AgentLoop(diagnoser.store, diagnoser, timedelta(seconds=settings.diagnose_delay_s)).run_forever(
        settings.poll_interval_s
    )
    return 0


def run_diagnose(args: argparse.Namespace) -> int:
    import logging

    logging.basicConfig(level=logging.ERROR)  # gaps in the evidence are shown as notes
    settings = Settings.from_env()
    diagnoser = _diagnoser(settings)
    if args.dry_run:
        evidence, messages = diagnoser.prompt(args.id)
        print(messages[1]["content"])
        chars = sum(len(m["content"]) for m in messages)
        print(
            f"\n-- {len(evidence.items)} evidence items, {chars} characters "
            f"(~{chars // 4} tokens) + schema. No model call."
        )
        return 0
    print(f"diagnosing incident {args.id} with {settings.ollama_model} ...", flush=True)
    report = diagnoser.diagnose(args.id, store_result=not args.no_store)
    print_diagnosis(report.payload())
    return 0 if report.valid else 2


def print_diagnosis(p: dict[str, Any]) -> None:
    d = p["diagnosis"]
    agree = "agrees" if p["agrees_with_baseline"] else "DISAGREES, check by hand"
    print(
        f"  model       : {p['model']}, {p['seconds']} s, {p['prompt_tokens']} prompt + "
        f"{p['output_tokens']} output tokens, attempts {p['attempts']}"
    )
    print(
        f"  root cause  : {d['root_cause_service']} ({d['category']}), "
        f"confidence {d['confidence']:.2f}{'' if p['valid'] else '  [INVALID]'}"
    )
    print(f"  summary     : {d['summary']}")
    dropped = d.get("dropped_symptoms") or []
    note = f"  (dropped, no alert: {', '.join(dropped)})" if dropped else ""
    print(f"  symptoms    : {', '.join(d['symptoms']) or '-'}{note}")
    print(f"  evidence    : {', '.join(d['evidence']) or '-'}")
    print(
        f"  action      : {d['recommended_action']} {d['action_target']} "
        f"(runbook {d['runbook']}) - suggestion only, nothing was executed"
    )
    print(f"  baseline    : {p['baseline_service']} ({p['baseline_rule']}) -> {agree}")
    if p["errors"]:
        print(f"  errors      : {'; '.join(p['errors'])}")


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
            if event["kind"] == "diagnosis":
                at = event["at"].astimezone(UTC)
                print(f"{at:%H:%M:%S}  diagnosis")
                print_diagnosis(payload)
                continue
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
    if args.command == "agent":
        return run_agent()
    if args.command == "diagnose":
        return run_diagnose(args)
    print(f"centinela {__version__} - phase 6: AI diagnosis (read-only) (try: centinela --help)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
