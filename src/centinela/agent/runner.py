"""The agent process: diagnose every new incident automatically, once.

    centinela agent

It waits DIAGNOSE_DELAY_S after detection so the symptoms of a cascade (UpstreamErrors,
HighErrorRate on orders) have time to join the incident before the model looks at it.
One incident at a time: the GPU runs one model call at a time anyway.
"""

import logging
import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import FrameType

from prometheus_client import Counter, Histogram

from centinela.actions.policy import KINDS, PolicyError, check
from centinela.actions.store import ActionStore
from centinela.agent.diagnosis import Diagnoser, Report
from centinela.incidents.store import IncidentStore

log = logging.getLogger("centinela.agent")

DIAGNOSES = Counter(
    "centinela_diagnoses_total", "Automatic diagnoses by result.", ["result"]
)  # valid | invalid | error
DIAGNOSIS_SECONDS = Histogram(
    "centinela_diagnosis_seconds",
    "Model time per diagnosis.",
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300),
)
LLM_TOKENS = Counter("centinela_llm_tokens_total", "Tokens processed by the model.", ["kind"])
PROPOSALS = Counter("centinela_action_proposals_total", "Actions proposed to a human.", ["kind"])


class AgentLoop:
    def __init__(
        self,
        store: IncidentStore,
        diagnoser: Diagnoser,
        delay: timedelta = timedelta(seconds=60),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        actions: ActionStore | None = None,
    ) -> None:
        self.store = store
        self.diagnoser = diagnoser
        self.delay = delay
        self.clock = clock
        self.actions = actions  # None: diagnose only (Phase 6 behaviour)
        self.done: set[int] = set()

    def pending(self) -> list[int]:
        now = self.clock()
        ready: list[int] = []
        for open_incident in self.store.open_incidents():
            incident_id = open_incident.id
            if incident_id in self.done:
                continue
            incident = self.store.get(incident_id)
            if incident is None or now - incident.detected_at < self.delay:
                continue
            if any(e["kind"] == "diagnosis" for e in self.store.events(incident_id)):
                self.done.add(incident_id)  # diagnosed before a restart
                continue
            ready.append(incident_id)
        return ready

    def cycle(self) -> list[int]:
        diagnosed: list[int] = []
        for incident_id in self.pending():
            self.done.add(incident_id)  # never retry in a loop, even on failure
            try:
                report = self.diagnoser.diagnose(incident_id)
            except Exception:
                DIAGNOSES.labels("error").inc()
                log.exception("diagnosis failed", extra={"fields": {"incident_id": incident_id}})
                continue
            DIAGNOSES.labels("valid" if report.valid else "invalid").inc()
            DIAGNOSIS_SECONDS.observe(report.seconds)
            LLM_TOKENS.labels("prompt").inc(report.prompt_tokens)
            LLM_TOKENS.labels("output").inc(report.output_tokens)
            self.propose(report)
            diagnosed.append(incident_id)
        return diagnosed

    def propose(self, report: Report) -> None:
        """Turn a trustworthy diagnosis into a PENDING action. A human still has to approve."""
        d = report.diagnosis
        if self.actions is None or not report.valid or d.recommended_action not in KINDS:
            return
        try:
            proposal = check(d.recommended_action, d.action_target)
        except PolicyError as exc:
            log.info("action not proposed", extra={"fields": {"reason": str(exc)}})
            return
        second_opinion = (
            "baseline rule agrees"
            if report.agrees_with_baseline
            else f"WARNING: baseline rule blames {report.baseline_service}"
        )
        reason = f"{d.summary} (confidence {d.confidence:.2f}, {second_opinion})"
        action = self.actions.propose(
            report.incident_id, proposal, reason[:500], "centinela-agent", self.clock()
        )
        if action is not None:
            PROPOSALS.labels(action.kind).inc()
            log.warning(
                "action waiting for human approval",
                extra={"fields": {"action_id": action.id, "action": action.describe()}},
            )

    def run_forever(self, interval_s: float) -> None:
        stop = {"now": False}

        def handle_stop(signum: int, frame: FrameType | None) -> None:
            stop["now"] = True

        signal.signal(signal.SIGTERM, handle_stop)
        log.info("agent started", extra={"fields": {"delay_s": self.delay.total_seconds()}})
        while not stop["now"]:
            started = time.monotonic()
            try:
                self.cycle()
            except Exception:
                log.exception("agent cycle failed")
            time.sleep(max(0.0, interval_s - (time.monotonic() - started)))
        log.info("agent stopped")
