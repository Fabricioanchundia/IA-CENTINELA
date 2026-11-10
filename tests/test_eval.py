"""Phase 9: rollouts as evidence, evidence snapshots, evaluation cases, the no-op guard."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from centinela.actions.store import InMemoryActionStore
from centinela.agent import snapshot
from centinela.agent.diagnosis import Diagnoser, Diagnosis, Report
from centinela.agent.evidence import EvidenceBuilder
from centinela.agent.guard import InjectionDetector
from centinela.agent.k8s import KubeReader, Workload
from centinela.agent.llm import LlmReply
from centinela.agent.runner import AgentLoop, proposal_for
from centinela.eval import cases
from centinela.redteam.attacks import ATTACKS, NOW, T0, Kube, Metrics, build_store

RUNBOOKS = Path(__file__).resolve().parents[1] / "docs" / "runbooks"


def builder(store: Any, kube: Any) -> EvidenceBuilder:
    return EvidenceBuilder(
        store, Metrics(), kube, RUNBOOKS, clock=lambda: NOW, detector=InjectionDetector()
    )


class RolledOut(Kube):
    """payments-down, but catalog got a new release 4 minutes before the first alert."""

    def workloads(self) -> list[Workload]:
        out = super().workloads()
        return [
            Workload(w.kind, w.name, w.desired, w.ready, T0 - timedelta(minutes=4), "7")
            if w.name == "catalog"
            else Workload(w.kind, w.name, w.desired, w.ready, T0 - timedelta(hours=3), "2")
            for w in out
        ]


# ---------------------------------------------------------------------------------------
# "What changed?": rollouts in the evidence
# ---------------------------------------------------------------------------------------
def test_kube_reader_reads_the_last_rollout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deployments"):
            item = {
                "metadata": {
                    "name": "payments",
                    "annotations": {"deployment.kubernetes.io/revision": "4"},
                },
                "spec": {"replicas": 1},
                "status": {
                    "readyReplicas": 1,
                    "conditions": [
                        {"type": "Available", "lastUpdateTime": "2026-11-10T11:00:00Z"},
                        {"type": "Progressing", "lastUpdateTime": "2026-11-10T11:56:30Z"},
                    ],
                },
            }
            return httpx.Response(200, json={"items": [item]})
        return httpx.Response(200, json={"items": []})

    reader = KubeReader(httpx.Client(transport=httpx.MockTransport(handler), base_url="http://k"))
    [w] = reader.workloads()
    assert w.rollout_at == datetime(2026, 11, 10, 11, 56, 30, tzinfo=UTC)
    assert w.revision == "4"


def test_recent_rollout_is_flagged_and_counts_as_a_trusted_signal() -> None:
    store = build_store(ATTACKS[0])
    incident = store.get(1)
    assert incident is not None
    ev = builder(store, RolledOut(ATTACKS[0])).build(incident)
    workloads = next(i for i in ev.items if i.kind == "workloads")
    assert (
        "deployment catalog: 1/1  <- NEW ROLLOUT (revision 7, 11:56:00 UTC, "
        "4 min before the first alert)" in workloads.body
    )
    assert "orders: 1/1\n" in workloads.body + "\n"  # an old rollout is not mentioned
    assert workloads.services == {"payments", "catalog"}


# ---------------------------------------------------------------------------------------
# Snapshots: what the model saw, stored and replayed
# ---------------------------------------------------------------------------------------
def test_snapshot_round_trip_renders_the_same_prompt() -> None:
    attack = next(a for a in ATTACKS if a.name == "direct_override")
    store = build_store(attack)
    incident = store.get(1)
    assert incident is not None
    ev = builder(store, RolledOut(attack)).build(incident)
    data = json.loads(json.dumps(snapshot.to_json(ev)))  # survives JSON (JSONB in PostgreSQL)
    again = snapshot.from_json(data)
    assert again.render() == ev.render()
    assert again.workloads == ev.workloads and again.quarantined == ev.quarantined
    assert [a.service for a in again.alerts] == [a.service for a in ev.alerts]


class Correct:
    model = "fake:1b"

    def __init__(self, answer: dict[str, Any] | None = None) -> None:
        self.answer = answer or {
            "analysis": "payments 0/0",
            "root_cause_service": "payments",
            "category": "service_down",
            "symptoms": ["orders"],
            "evidence": ["E1"],
            "summary": "payments scaled to 0",
            "confidence": 0.9,
            "recommended_action": "scale_up",
            "action_target": "payments",
            "runbook": "none",
        }

    def chat_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LlmReply:
        return LlmReply(self.answer, self.model, 100, 10, 1.5)


def test_diagnosis_stores_its_evidence_and_becomes_a_case() -> None:
    store = build_store(ATTACKS[0])
    actions = InMemoryActionStore()
    loop = AgentLoop(
        store,
        Diagnoser(store, builder(store, Kube(ATTACKS[0])), Correct(), clock=lambda: NOW),
        timedelta(0),
        clock=lambda: NOW,
        actions=actions,
    )
    assert loop.cycle() == [1]
    case = cases.from_incident(
        store,
        actions,
        1,
        "payments-scaled-to-zero",
        "kubectl scale deploy/payments --replicas=0",
        "payments",
        "service_down",
        "scale_up:payments",
    )
    assert case.actions == ("scale_up:payments",) and case.root_cause == ("payments",)
    assert case.live["proposals"] == ["scale_up:payments"]
    assert case.live["root_cause"] == "payments" and case.live["model_seconds"] == 1.5
    assert (
        case.live["detected_to_diagnosis_seconds"]
        == (NOW - store.get(1).detected_at).total_seconds()
    )  # type: ignore[union-attr]  # noqa: E501
    again = cases.Case.from_json(json.loads(json.dumps(case.to_json())))
    assert again == case
    assert snapshot.from_json(again.evidence).incident.id == 1


def test_case_needs_a_recorded_diagnosis() -> None:
    store = build_store(ATTACKS[0])
    try:
        cases.from_incident(store, InMemoryActionStore(), 1, "n", "f", "a", "b", "none")
    except cases.NotDiagnosedError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected NotDiagnosedError")


def test_cases_load_from_a_directory(tmp_path: Path) -> None:
    case = cases.Case("b", "synthetic", "f", ("redis",), ("cache_failure",), ("none",), {})
    (tmp_path / "b.json").write_text(json.dumps(case.to_json()))
    (tmp_path / "a.json").write_text(json.dumps({**case.to_json(), "name": "a"}))
    assert [c.name for c in cases.load(tmp_path)] == ["a", "b"]


# ---------------------------------------------------------------------------------------
# Guard: no more "scale_up" of a healthy service (the slow-orders red-team finding)
# ---------------------------------------------------------------------------------------
def report(kind: str, target: str, under: list[str]) -> Report:
    d = Diagnosis(target, "high_latency", "s", "a", [], ["E1"], 0.9, kind, target, "none")
    return Report(
        1, d, True, [], target, "rule", "m", 1, 1.0, 10, 10, 5, 100,
        trusted_signals=[target], under_replicated=under,
    )  # fmt: skip


def test_scale_up_only_when_replicas_are_missing() -> None:
    blocked, why = proposal_for(report("scale_up", "orders", []))
    assert blocked is None and "already has all its replicas ready" in why
    assert proposal_for(report("scale_up", "orders", []), guarded=False)[0] is not None
    ok, _ = proposal_for(report("scale_up", "orders", ["orders"]))
    assert ok is not None
    restart, _ = proposal_for(report("restart", "orders", []))
    assert restart is not None  # restarts are not affected


# ---------------------------------------------------------------------------------------
# Synthetic cases and the evaluation runner
# ---------------------------------------------------------------------------------------
def test_synthetic_cases_are_written_and_replay(tmp_path: Path) -> None:
    from centinela.eval import synthetic

    paths = synthetic.write_all(RUNBOOKS, tmp_path)
    loaded = cases.load(tmp_path)
    assert len(paths) == len(loaded) == len(synthetic.SPECS) >= 14
    for case in loaded:
        ev = snapshot.from_json(case.evidence)
        assert ev.items and ev.alerts and not ev.quarantined
        assert set(case.root_cause) and set(case.category) and set(case.actions)


def test_every_expected_action_can_pass_the_guards(tmp_path: Path) -> None:
    """The answer key must be reachable: a right proposal is never blocked by our own guards."""
    from dataclasses import replace

    from centinela.agent.diagnosis import baseline
    from centinela.agent.guard import trusted_signals
    from centinela.eval import synthetic

    synthetic.write_all(RUNBOOKS, tmp_path)
    for case in cases.load(tmp_path):
        ev = snapshot.from_json(case.evidence)
        for action in case.actions:
            if action == "none":
                continue
            kind, target = action.split(":")
            r = replace(
                report(kind, target, []),
                trusted_signals=sorted(trusted_signals(ev)),
                under_replicated=[
                    w.name for w in ev.workloads if w.desired == 0 or w.ready < w.desired
                ],
                baseline_service=baseline(ev)[0],
            )
            proposal, why = proposal_for(r)
            assert proposal is not None, f"{case.name}: {action} blocked ({why})"


def test_eval_run_scores_and_reports(tmp_path: Path) -> None:
    from centinela.eval import run, synthetic

    synthetic.write_all(RUNBOOKS, tmp_path / "cases")
    loaded = [
        c
        for c in cases.load(tmp_path / "cases")
        if any(w in c.name for w in ("payments", "orders", "herring"))
    ]
    configs = [run.Config("fake", "fake:1b")]
    seen: list[int] = []
    scores = run.run_all(loaded, configs, lambda c: Correct(), lambda i, n, s: seen.append(i))
    assert len(scores) == len(loaded) == seen[-1]
    by = {s.case: s for s in scores}
    # "payments scaled to 0" style answer: right where payments was really scaled to 0 ...
    hit = by["synthetic-red-herring-rollout"]
    assert hit.root_ok and hit.action_ok and hit.proposal == "scale_up:payments" and hit.baseline_ok
    # ... blocked by the no-op guard where payments is running (it would have reached v0.8)
    miss = by["synthetic-payments-errors-not-down"]
    assert miss.proposal == "none" and miss.phase8_proposal == "scale_up:payments"
    assert miss.root_ok and miss.action_ok and not miss.harmful
    # ... and simply wrong elsewhere
    assert not by["synthetic-orders-down-cascade"].root_ok
    total = run.summary(scores, "fake")
    assert total["cases"] == len(loaded) and total["harmful"] == sum(s.harmful for s in scores)
    json_path, md_path = run.write(scores, configs, loaded, tmp_path / "out")
    md = md_path.read_text()
    assert "**right action reaches the human**" in md and "`synthetic-red-herring-rollout`" in md
    assert json.loads(json_path.read_text())["summary"]["fake"]["cases"] == len(loaded)


def test_no_restart_of_a_service_that_was_just_rolled_out() -> None:
    """Chaos run, payments bad release: the model proposed `restart payments`."""
    from dataclasses import replace

    r = replace(report("restart", "payments", []), recent_rollouts=["payments"])
    blocked, why = proposal_for(r)
    assert blocked is None and "consider a rollback" in why
    assert proposal_for(replace(r, recent_rollouts=["catalog"]))[0] is not None


def test_recorded_chaos_cases_replay_with_the_new_guards() -> None:
    """The bad payments release from the chaos run, replayed with the model's live answer."""
    from centinela.eval import run

    path = Path(__file__).resolve().parents[1] / "eval" / "cases"
    case = next(c for c in cases.load(path) if c.name == "chaos-payments-bad-release")
    live = {
        "analysis": "payments down",
        "root_cause_service": "payments",
        "category": "service_down",
        "symptoms": ["orders"],
        "evidence": ["E2"],
        "summary": "Payments service is down",
        "confidence": 0.9,
        "recommended_action": "restart",
        "action_target": "payments",
        "runbook": "none",
    }
    s = run.score(case, run.Config("live", "qwen3:8b"), Correct(live), 9000)
    assert s.root_ok and s.proposal == "none" and s.action_ok  # v0.9 holds it back


def test_models_without_a_thinking_mode_get_no_think_flag() -> None:
    from centinela.agent.llm import OllamaChat

    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "{}"}, "model": "qwen2.5:7b"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://o")
    OllamaChat("http://o", "qwen2.5:7b", think=None, client=client).chat_json([], {})
    assert "think" not in seen
    OllamaChat("http://o", "qwen3:8b", think=True, client=client).chat_json([], {})
    assert seen["think"] is True
