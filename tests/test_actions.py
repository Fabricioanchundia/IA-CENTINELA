import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from centinela.actions.approvals import ApprovalError, approve, reject
from centinela.actions.executor import DeploymentState, Executor, KubeShopWriter
from centinela.actions.policy import PENDING_TTL, PolicyError, Proposal, check
from centinela.actions.store import InMemoryActionStore
from centinela.agent.diagnosis import Diagnoser
from centinela.agent.runner import AgentLoop
from tests.test_agent import DETECTED, FakeLlm, builder, good_answer, make_store

NOW = datetime(2026, 11, 9, 21, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------------------
def test_policy_allows_only_the_allowlist() -> None:
    assert check("scale_up", "payments") == Proposal("scale_up", "payments", {"replicas": 1})
    assert check("restart", "redis") == Proposal("restart", "redis", {})


@pytest.mark.parametrize(
    ("kind", "target", "params", "error"),
    [
        ("delete", "payments", None, "not allowed"),
        ("rollback", "payments", None, "not allowed"),
        ("scale_up", "postgres", None, "target 'postgres'"),  # never the database
        ("scale_up", "kube-system", None, "not allowed"),
        ("scale_up", "payments", {"replicas": 50}, "between 1 and 3"),
        ("scale_up", "payments", {"replicas": 0}, "between 1 and 3"),
        ("scale_up", "payments", {"replicas": True}, "integer"),
        ("restart", "orders", {"force": True}, "no parameters"),
    ],
)
def test_policy_rejects(kind: str, target: str, params: dict[str, Any] | None, error: str) -> None:
    with pytest.raises(PolicyError, match=error):
        check(kind, target, params)


# ---------------------------------------------------------------------------------------
# Store rules and human decisions
# ---------------------------------------------------------------------------------------
def proposed(store: InMemoryActionStore, incident: int = 1, target: str = "payments") -> int:
    action = store.propose(
        incident, check("scale_up", target), "payments down", "centinela-agent", NOW
    )
    assert action is not None
    return action.id


def test_one_open_action_per_incident() -> None:
    store = InMemoryActionStore()
    proposed(store)
    assert store.propose(1, check("restart", "orders"), "x", "agent", NOW) is None
    assert proposed(store, incident=2) == 2


def test_human_approval_is_recorded() -> None:
    store = InMemoryActionStore()
    action = approve(store, proposed(store), "Fabricio", NOW, "looks right")
    assert (action.status, action.decided_by, action.decision_note) == (
        "approved",
        "Fabricio",
        "looks right",
    )
    assert [e[1] for e in store.events] == ["action_proposed", "action_approved"]


@pytest.mark.parametrize("name", ["agent", "centinela-agent", "System", "x", "", "rm -rf /;"])
def test_machines_and_odd_names_cannot_approve(name: str) -> None:
    store = InMemoryActionStore()
    with pytest.raises(ApprovalError):
        approve(store, proposed(store), name, NOW)


def test_reject_needs_a_reason() -> None:
    store = InMemoryActionStore()
    action_id = proposed(store)
    with pytest.raises(ApprovalError, match="why"):
        reject(store, action_id, "Fabricio", "  ", NOW)
    assert reject(store, action_id, "Fabricio", "it was a drill", NOW).status == "rejected"


def test_decisions_explain_why_they_fail() -> None:
    store = InMemoryActionStore()
    action_id = proposed(store)
    approve(store, action_id, "Fabricio", NOW)
    with pytest.raises(ApprovalError, match="already approved"):
        reject(store, action_id, "Ana", "no", NOW)
    with pytest.raises(ApprovalError, match="does not exist"):
        approve(store, 99, "Fabricio", NOW)
    late = proposed(store, incident=2)
    with pytest.raises(ApprovalError, match="expired"):
        approve(store, late, "Fabricio", NOW + PENDING_TTL + timedelta(seconds=1))


def test_pending_actions_expire() -> None:
    store = InMemoryActionStore()
    proposed(store)
    assert store.expire(NOW + timedelta(minutes=5)) == []
    [expired] = store.expire(NOW + PENDING_TTL)
    assert expired.status == "expired"
    assert proposed(store) == 2  # the incident can get a new proposal afterwards


# ---------------------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------------------
class FakeShop:
    def __init__(self, desired: int = 0, ready: int = 0, comes_up: bool = True) -> None:
        self.desired, self.ready, self.comes_up = desired, ready, comes_up
        self.calls: list[str] = []
        self.fail = False

    def state(self, name: str) -> DeploymentState:
        if self.fail:
            raise httpx.ConnectError("api server down")
        return DeploymentState(name, self.desired, self.ready)

    def scale(self, name: str, replicas: int) -> None:
        self.calls.append(f"scale {name} {replicas}")
        self.desired = replicas
        if self.comes_up:
            self.ready = replicas

    def restart(self, name: str, at: datetime) -> None:
        self.calls.append(f"restart {name}")


def executor(store: InMemoryActionStore, shop: FakeShop, **kw: Any) -> Executor:
    return Executor(store, shop, clock=lambda: NOW, sleep=lambda s: None, **kw)


def approved(store: InMemoryActionStore, incident: int = 1, kind: str = "scale_up") -> int:
    action = store.propose(incident, check(kind, "payments"), "why", "centinela-agent", NOW)
    assert action is not None
    approve(store, action.id, "Fabricio", NOW)
    return action.id


def test_nothing_runs_without_approval() -> None:
    store, shop = InMemoryActionStore(), FakeShop()
    proposed(store)
    assert executor(store, shop).cycle() == []
    assert shop.calls == []


def test_approved_scale_up_runs_and_is_verified() -> None:
    store, shop = InMemoryActionStore(), FakeShop()
    approved(store)
    [done] = executor(store, shop).cycle()
    assert shop.calls == ["scale payments 1"]
    assert done.status == "executed" and "0/0 -> 1/1 ready" in (done.result or "")
    assert store.events[-1][1] == "action_executed"


def test_not_needed_anymore_is_skipped() -> None:
    store, shop = InMemoryActionStore(), FakeShop(desired=1, ready=1)
    approved(store)
    [done] = executor(store, shop).cycle()
    assert done.status == "skipped" and shop.calls == []


def test_dry_run_changes_nothing() -> None:
    store, shop = InMemoryActionStore(), FakeShop()
    approved(store)
    [done] = executor(store, shop, dry_run=True).cycle()
    assert done.status == "skipped" and "dry run" in (done.result or "") and shop.calls == []


def test_restart() -> None:
    store, shop = InMemoryActionStore(), FakeShop(desired=1, ready=1)
    approved(store, kind="restart")
    [done] = executor(store, shop).cycle()
    assert shop.calls == ["restart payments"] and done.status == "executed"


def test_policy_is_checked_again_before_acting() -> None:
    store, shop = InMemoryActionStore(), FakeShop()
    action_id = approved(store)
    store.actions[action_id] = replace(store.actions[action_id], target="postgres")  # tampered
    [done] = executor(store, shop).cycle()
    assert done.status == "failed" and "policy" in (done.result or "") and shop.calls == []


def test_rate_limit_caps_the_blast_radius() -> None:
    store, shop = InMemoryActionStore(), FakeShop()
    for incident in (1, 2, 3):
        approved(store, incident)
        shop.desired = shop.ready = 0
        executor(store, shop).cycle()
    approved(store, 4)
    shop.desired = shop.ready = 0
    [done] = executor(store, shop).cycle()
    assert done.status == "failed" and "rate limit" in (done.result or "")


def test_kubernetes_errors_and_failed_verification_are_failures() -> None:
    store, shop = InMemoryActionStore(), FakeShop()
    approved(store)
    shop.fail = True
    [done] = executor(store, shop).cycle()
    assert done.status == "failed" and "kubernetes error" in (done.result or "")

    store, shop = InMemoryActionStore(), FakeShop(comes_up=False)
    approved(store)
    [done] = executor(store, shop, verify_timeout_s=0.01).cycle()
    assert done.status == "failed" and "only 0/1 ready" in (done.result or "")


def test_expired_actions_are_closed_by_the_executor() -> None:
    store, shop = InMemoryActionStore(), FakeShop()
    proposed(store)
    later = Executor(store, shop, clock=lambda: NOW + PENDING_TTL, sleep=lambda s: None)
    assert later.cycle() == []
    assert store.actions[1].status == "expired"


# ---------------------------------------------------------------------------------------
# Kubernetes requests
# ---------------------------------------------------------------------------------------
def test_kube_writer_sends_minimal_merge_patches() -> None:
    seen: list[tuple[str, str, str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append(
            (request.method, request.url.path, request.headers.get("content-type", ""), body)
        )
        return httpx.Response(200, json={"spec": {"replicas": 0}, "status": {}})

    writer = KubeShopWriter(
        httpx.Client(base_url="https://k8s", transport=httpx.MockTransport(handler))
    )
    assert writer.state("payments") == DeploymentState("payments", 0, 0)
    writer.scale("payments", 1)
    writer.restart("orders", NOW)
    base = "/apis/apps/v1/namespaces/shop/deployments"
    assert seen[1][:3] == ("PATCH", f"{base}/payments/scale", "application/merge-patch+json")
    assert seen[1][3] == {"spec": {"replicas": 1}}
    annotations = seen[2][3]["spec"]["template"]["metadata"]["annotations"]
    assert seen[2][1] == f"{base}/orders" and "kubectl.kubernetes.io/restartedAt" in annotations


def test_executor_needs_the_cluster(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("centinela.actions.executor.SA_DIR", tmp_path)
    assert KubeShopWriter.in_cluster() is None


# ---------------------------------------------------------------------------------------
# The agent proposes, never decides
# ---------------------------------------------------------------------------------------
def agent_loop(answer: dict[str, Any], runbooks: Path) -> tuple[AgentLoop, InMemoryActionStore]:
    store, actions = make_store(), InMemoryActionStore()
    diagnoser = Diagnoser(store, builder(store, runbooks), FakeLlm([answer, answer]))
    loop = AgentLoop(
        store,
        diagnoser,
        timedelta(0),
        clock=lambda: DETECTED + timedelta(minutes=2),
        actions=actions,
    )
    return loop, actions


@pytest.fixture
def runbooks(tmp_path: Path) -> Path:
    return tmp_path


def test_agent_proposes_a_pending_action(runbooks: Path) -> None:
    loop, actions = agent_loop(good_answer(), runbooks)
    assert loop.cycle() == [1]
    [action] = actions.recent()
    assert (action.status, action.kind, action.target, action.proposed_by) == (
        "pending",
        "scale_up",
        "payments",
        "centinela-agent",
    )
    assert "confidence 0.90, baseline rule agrees" in action.reason


@pytest.mark.parametrize(
    "answer",
    [
        good_answer(recommended_action="investigate", action_target="none"),
        good_answer(recommended_action="scale_up", action_target="postgres"),  # policy says no
        good_answer(evidence=["E99"]),  # invalid diagnosis: nothing to act on
    ],
)
def test_agent_does_not_propose_when_it_should_not(answer: dict[str, Any], runbooks: Path) -> None:
    loop, actions = agent_loop(answer, runbooks)
    loop.cycle()
    assert actions.recent() == []
