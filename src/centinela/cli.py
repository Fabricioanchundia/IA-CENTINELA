"""Command-line entry point.

centinela collect                       run the incident collector (in the cluster)
centinela agent                         diagnose new incidents automatically (in the cluster)
centinela diagnose ID [--dry-run]       diagnose one incident now (--dry-run: show the prompt)
centinela actions [--all]               actions waiting for a human (--all: history too)
centinela approve ID --by NAME          approve an action; the executor runs it
centinela reject ID --by NAME --note .. reject an action, with the reason
centinela executor                      run approved actions (in the cluster, narrow permissions)
centinela console                       web page to approve or reject actions (port 8000)
centinela mcp                           MCP server on stdio: read incidents, propose actions
centinela redteam                       prompt-injection attacks vs the agent (needs only Ollama)
centinela incidents list                recent incidents with MTTD / MTTR
centinela incidents show ID             timeline: alerts, logs, resolution
centinela incidents close ID --cause .. --resolution ..   write the postmortem
centinela incidents stats               averages for the whole history
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from centinela import __version__
from centinela.config import Settings
from centinela.incidents.models import Incident
from centinela.incidents.store import PostgresIncidentStore, make_pool

if TYPE_CHECKING:
    from centinela.actions.models import Action
    from centinela.actions.store import PostgresActionStore
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
    acts = sub.add_parser("actions", help="actions proposed by the agent")
    acts.add_argument("--all", action="store_true", help="include decided and finished ones")
    acts.add_argument("--limit", type=int, default=20)
    appr = sub.add_parser("approve", help="approve a pending action")
    appr.add_argument("id", type=int)
    appr.add_argument("--by", required=True, help="your name (recorded in the audit trail)")
    appr.add_argument("--note", default=None)
    rej = sub.add_parser("reject", help="reject a pending action")
    rej.add_argument("id", type=int)
    rej.add_argument("--by", required=True)
    rej.add_argument("--note", required=True, help="why (goes into the postmortem)")
    sub.add_parser("executor", help="run approved actions")
    sub.add_parser("console", help="web page to approve or reject actions")
    sub.add_parser("mcp", help="MCP server (stdio): read incidents, propose actions")
    red = sub.add_parser(
        "redteam", help="prompt-injection attacks against the agent (needs only Ollama)"
    )
    red.add_argument("--levels", default="L0,L1,L2", help="defence levels to test")
    red.add_argument("--attacks", default="", help="comma-separated attack names (default all)")
    red.add_argument(
        "--scenario", default="all", help="payments-down, slow-orders or all (default)"
    )
    red.add_argument("--out", default="docs/redteam", help="where to write the results")
    red.add_argument("--list", action="store_true", help="list the attacks and exit")

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
    store.migrate(settings.reader_password, settings.console_db_password, settings.mcp_db_password)
    start_http_server(settings.metrics_port)
    Collector(
        PrometheusAlerts(settings.prometheus_url), store, LokiLogs(settings.loki_url)
    ).run_forever(settings.poll_interval_s)
    return 0


def _diagnoser(settings: Settings, store: PostgresIncidentStore | None = None) -> "Diagnoser":
    from pathlib import Path

    from centinela.agent.diagnosis import Diagnoser
    from centinela.agent.evidence import EvidenceBuilder
    from centinela.agent.guard import InjectionDetector
    from centinela.agent.k8s import KubeReader
    from centinela.agent.llm import OllamaChat
    from centinela.incidents.sources import PrometheusQuery

    store = store or PostgresIncidentStore(make_pool(settings.database_url))
    builder = EvidenceBuilder(
        store,
        PrometheusQuery(settings.prometheus_url),
        KubeReader.in_cluster(settings.shop_namespace),
        Path(settings.runbooks_dir),
        detector=InjectionDetector(),
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
    store = PostgresIncidentStore(make_pool(settings.database_url))
    diagnoser = _diagnoser(settings, store)
    start_http_server(settings.metrics_port)
    actions = None
    if settings.propose_actions:
        from centinela.actions.store import PostgresActionStore

        actions = PostgresActionStore(store.pool)
    AgentLoop(
        store,
        diagnoser,
        timedelta(seconds=settings.diagnose_delay_s),
        actions=actions,
    ).run_forever(settings.poll_interval_s)
    return 0


def _actions() -> "PostgresActionStore":
    from centinela.actions.store import PostgresActionStore

    return PostgresActionStore(make_pool(Settings.from_env().database_url))


def print_actions(rows: list["Action"]) -> None:
    print(f"{'ID':>4}  {'INC':>4}  {'STATUS':9}  {'PROPOSED':8}  {'EXPIRES':8}  ACTION")
    pad = " " * 44
    for a in rows:
        proposed = a.proposed_at.astimezone(UTC)
        expires = a.expires_at.astimezone(UTC)
        print(
            f"{a.id:>4}  {a.incident_id:>4}  {a.status:9}  {proposed:%H:%M:%S}  "
            f"{expires:%H:%M:%S}  {a.describe()}"
        )
        print(f"{pad}why: {a.reason}")
        if a.decided_by:
            note = f": {a.decision_note}" if a.decision_note else ""
            verb = "rejected" if a.status == "rejected" else "approved"  # the human decision
            print(f"{pad}{verb} by {a.decided_by}{note}")
        if a.result:
            print(f"{pad}result: {a.result}")


def run_actions(args: argparse.Namespace) -> int:
    from centinela.actions.approvals import ApprovalError, approve, reject

    store = _actions()
    now = datetime.now(UTC)
    if args.command == "actions":
        rows = store.recent(args.limit, open_only=not args.all)
        if not rows:
            print("no actions yet" if args.all else "no actions waiting for a decision")
            return 0
        print_actions(rows)
        return 0
    try:
        if args.command == "approve":
            action = approve(store, args.id, args.by, now, args.note)
            print(f"action {action.id} approved by {action.decided_by}: the executor runs it now")
        else:
            action = reject(store, args.id, args.by, args.note, now)
            print(f"action {action.id} rejected by {action.decided_by}")
    except ApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def run_console() -> int:
    import os

    import uvicorn

    from centinela.actions.store import PostgresActionStore
    from centinela.console.app import ConsoleConfig, create_app
    from centinela.logs import configure_logging

    configure_logging("centinela-console")
    user = os.environ.get("CONSOLE_USER", "")
    password = os.environ.get("CONSOLE_PASSWORD", "")
    secret = os.environ.get("CONSOLE_SECRET_KEY", "")
    if len(password) < 16 or len(secret) < 32 or not user:
        print(
            "error: set CONSOLE_USER, CONSOLE_PASSWORD (16+ chars) and CONSOLE_SECRET_KEY "
            "(32+ chars); Terraform generates them",
            file=sys.stderr,
        )
        return 1
    pool = make_pool(Settings.from_env().database_url)
    config = ConsoleConfig(
        user=user,
        password=password,
        secret_key=secret.encode(),
        secure_cookies=os.environ.get("CONSOLE_SECURE_COOKIES", "false").lower() == "true",
    )
    app = create_app(PostgresIncidentStore(pool), PostgresActionStore(pool), config)
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False, server_header=False)  # noqa: S104 - inside its pod
    return 0


def run_mcp() -> int:
    import logging
    import os

    from centinela.actions.store import PostgresActionStore
    from centinela.mcp_server import build_server

    # stdout carries the MCP protocol: logs must go to stderr
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    pool = make_pool(Settings.from_env().database_url)
    server = build_server(
        PostgresIncidentStore(pool),
        PostgresActionStore(pool),
        client=os.environ.get("MCP_CLIENT_NAME", "mcp-client")[:40],
    )
    server.run("stdio")
    return 0


def run_executor() -> int:
    from prometheus_client import start_http_server

    from centinela.actions.executor import Executor, KubeShopWriter
    from centinela.logs import configure_logging

    configure_logging("centinela-executor")
    settings = Settings.from_env()
    writer = KubeShopWriter.in_cluster(settings.shop_namespace)
    if writer is None:
        print("error: the executor only runs inside the cluster", file=sys.stderr)
        return 1
    start_http_server(settings.metrics_port)
    Executor(_actions(), writer, dry_run=settings.executor_dry_run).run_forever(5)
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


def run_redteam(args: argparse.Namespace) -> int:
    import logging
    from pathlib import Path

    import httpx

    from centinela.agent.llm import OllamaChat
    from centinela.redteam.attacks import ATTACKS, SCENARIOS
    from centinela.redteam.run import LEVELS, Run, run_all, scenario_attacks, totals, verdict, write

    logging.basicConfig(level=logging.ERROR)
    if args.list:
        for a in ATTACKS:
            print(f"{a.name:<20} {a.where:<8} goal={a.goal:<9} {a.technique}")
        return 0
    wanted = {n.strip() for n in args.attacks.split(",") if n.strip()}
    attacks = [a for a in ATTACKS if not wanted or a.name in wanted]
    levels = [lv for lv in LEVELS if lv.key in {k.strip().upper() for k in args.levels.split(",")}]
    scenarios = [s for s in SCENARIOS if args.scenario in ("all", s.name)]
    unknown = wanted - {a.name for a in ATTACKS}
    if unknown or not attacks or not levels or not scenarios:
        print(f"nothing to run (unknown attacks: {', '.join(sorted(unknown)) or '-'})")
        return 2
    settings = Settings.from_env()
    runbooks = Path(settings.runbooks_dir)
    if not runbooks.is_dir():
        print(f"runbooks not found in {runbooks}: run this from the repository root")
        return 2
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=5).json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"cannot reach Ollama at {settings.ollama_url} ({type(exc).__name__}).")
        print("Is the Ollama app running on Windows? Try: curl -s localhost:11434/api/version")
        return 2
    models = {m.get("name") for m in tags.get("models", [])}
    if settings.ollama_model not in models:
        print(f"model {settings.ollama_model} is not in Ollama (have: {', '.join(sorted(models))})")
        return 2
    llm = OllamaChat(
        settings.ollama_url,
        settings.ollama_model,
        num_ctx=settings.ollama_num_ctx,
        think=settings.ollama_think,
    )
    total = sum(len(scenario_attacks(s, attacks)) for s in scenarios) * len(levels)
    print(
        f"red team: {total} runs ({', '.join(s.name for s in scenarios)}; {len(levels)} levels) "
        f"on {settings.ollama_model}, no cluster, no database. Loading the model ...",
        flush=True,
    )
    llm.warm()
    done = {"n": 0}

    def progress(i: int, n: int, run: Run) -> None:
        done["n"] += 1
        shield = " [quarantined]" if run.quarantined else ""
        print(
            f"[{done['n']:>2}/{total}] {run.level} {run.attack:<20} model: {run.model_action:<18} "
            f"-> {verdict(run)}{shield}  ({run.seconds:.0f}s)",
            flush=True,
        )

    for scenario in scenarios:
        print(f"\n== {scenario.name}: {scenario.title}", flush=True)
        runs = run_all(
            llm,
            runbooks,
            scenario,
            attacks,
            levels,
            progress,
            max_prompt_chars=(settings.ollama_num_ctx - 900) * 3,
        )
        print(f"\n{scenario.name}: right outcome = {scenario.expected_text}")
        print(f"{'level':<22}{'hijacked':>10}{'fooled':>9}{'quarantined':>13}{'right outcome':>15}")
        for lv in levels:
            t = totals(runs, lv.key)
            print(
                f"{lv.key + ' ' + lv.title:<22}{t['hijacked']:>6}/{t['attacks']:<3}"
                f"{t['fooled']:>5}/{t['attacks']:<3}{t['quarantined']:>9}/{t['attacks']:<3}"
                f"{t['expected']:>11}/{t['runs']:<3}"
            )
        json_path, md_path = write(runs, settings.ollama_model, scenario, Path(args.out))
        print(f"results: {md_path} (table) and {json_path} (every answer)")
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
            if event["kind"] == "diagnosis":
                at = event["at"].astimezone(UTC)
                print(f"{at:%H:%M:%S}  diagnosis")
                print_diagnosis(payload)
                continue
            if str(event["kind"]).startswith("action_"):
                at = event["at"].astimezone(UTC)
                extra = payload.get("result") or payload.get("note") or ""
                print(
                    f"{at:%H:%M:%S}  {event['kind']:18}  #{payload.get('action_id')} "
                    f"{payload.get('action')} (by {payload.get('by')})"
                    + (f": {extra}" if extra else "")
                )
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
    if args.command in {"actions", "approve", "reject"}:
        return run_actions(args)
    if args.command == "executor":
        return run_executor()
    if args.command == "console":
        return run_console()
    if args.command == "mcp":
        return run_mcp()
    if args.command == "redteam":
        return run_redteam(args)
    print(f"centinela {__version__} - phase 8: red team + injection guards (try: centinela --help)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
