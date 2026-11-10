"""Replay every case on one or more models and score the answers (Phase 9).

    centinela eval run --models qwen3:8b,qwen2.5:7b --think

For each case the recorded evidence goes through the production Diagnoser (same prompt, same
validation, same retry) and the production proposal guards. We score:

- root cause: the blamed component is one of the acceptable ones;
- category: idem;
- right action: what would reach the human (a proposal, or nothing) is one of the right ones;
- harmful: a proposal that is NOT one of the right ones (the human would see a wrong fix);
- the rule-based baseline on the same evidence, as the bar the model has to beat.
"""

import json
import statistics
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from centinela.agent import snapshot
from centinela.agent.diagnosis import Diagnoser
from centinela.agent.llm import JsonChat
from centinela.agent.runner import proposal_for
from centinela.eval.cases import Case
from centinela.incidents.store import IncidentStore


@dataclass(frozen=True)
class Config:
    label: str
    model: str
    think: bool | None = False  # None: the model has no thinking mode
    num_ctx: int = 4096


@dataclass
class Score:
    config: str
    case: str
    source: str
    root_cause: str
    category: str
    model_action: str
    proposal: str  # "kind:target" or "none"
    phase8_proposal: str  # the same answer with the v0.8 gate (no no-op guard)
    valid: bool
    root_ok: bool
    category_ok: bool
    action_ok: bool
    harmful: bool
    phase8_ok: bool
    phase8_harmful: bool
    baseline: str
    baseline_ok: bool
    seconds: float
    prompt_tokens: int
    output_tokens: int
    attempts: int
    summary: str


def score(case: Case, config: Config, llm: JsonChat, max_prompt_chars: int) -> Score:
    evidence = snapshot.from_json(case.evidence)
    store = cast(IncidentStore, snapshot.ReplayStore(evidence.incident))
    diagnoser = Diagnoser(
        store,
        snapshot.ReplayBuilder(case.evidence),
        llm,
        clock=lambda: evidence.observed_at,
        max_prompt_chars=max_prompt_chars,
    )
    report = diagnoser.diagnose(evidence.incident.id, store_result=False)
    d = report.diagnosis
    proposal, _ = proposal_for(report)
    proposed = f"{proposal.kind}:{proposal.target}" if proposal else "none"
    # What v0.8 would have proposed: the same answer without the Phase 9 guards (no-op
    # scale_up, restart right after a rollout).
    v08, _ = proposal_for(replace(report, under_replicated=[d.action_target], recent_rollouts=[]))
    phase8 = f"{v08.kind}:{v08.target}" if v08 else "none"
    return Score(
        config=config.label,
        case=case.name,
        source=case.source,
        root_cause=d.root_cause_service,
        category=d.category,
        model_action=f"{d.recommended_action}:{d.action_target}",
        proposal=proposed,
        phase8_proposal=phase8,
        valid=report.valid,
        root_ok=d.root_cause_service in case.root_cause,
        category_ok=d.category in case.category,
        action_ok=proposed in case.actions,
        harmful=proposed != "none" and proposed not in case.actions,
        phase8_ok=phase8 in case.actions,
        phase8_harmful=phase8 != "none" and phase8 not in case.actions,
        baseline=report.baseline_service,
        baseline_ok=report.baseline_service in case.root_cause,
        seconds=report.seconds,
        prompt_tokens=report.prompt_tokens,
        output_tokens=report.output_tokens,
        attempts=report.attempts,
        summary=d.summary,
    )


def run_all(
    cases: Sequence[Case],
    configs: Sequence[Config],
    make_llm: Callable[[Config], JsonChat],
    progress: Callable[[int, int, Score], None] | None = None,
) -> list[Score]:
    scores: list[Score] = []
    total = len(cases) * len(configs)
    for config in configs:
        llm = make_llm(config)
        budget = (config.num_ctx - 900) * 3
        for case in cases:
            s = score(case, config, llm, budget)
            scores.append(s)
            if progress:
                progress(len(scores), total, s)
    return scores


def _pct(n: int, total: int) -> str:
    return f"{n}/{total} ({100 * n / total:.0f}%)" if total else "-"


def summary(scores: Sequence[Score], label: str) -> dict[str, float | int | str]:
    rows = [s for s in scores if s.config == label]
    n = len(rows)
    secs = sorted(s.seconds for s in rows)
    return {
        "cases": n,
        "fully_right": sum(s.root_ok and s.action_ok and s.valid for s in rows),
        "root_cause": sum(s.root_ok for s in rows),
        "category": sum(s.category_ok for s in rows),
        "right_action": sum(s.action_ok for s in rows),
        "harmful": sum(s.harmful for s in rows),
        "right_action_v08": sum(s.phase8_ok for s in rows),
        "harmful_v08": sum(s.phase8_harmful for s in rows),
        "invalid": sum(not s.valid for s in rows),
        "baseline_root_cause": sum(s.baseline_ok for s in rows),
        "median_seconds": round(statistics.median(secs), 1) if secs else 0.0,
        "max_seconds": round(secs[-1], 1) if secs else 0.0,
        "mean_prompt_tokens": round(statistics.mean(s.prompt_tokens for s in rows)) if rows else 0,
    }


def markdown(scores: Sequence[Score], configs: Sequence[Config], cases: Sequence[Case]) -> str:
    labels = [c.label for c in configs]
    sums = {lb: summary(scores, lb) for lb in labels}
    n = len(cases)
    chaos = sum(c.source == "chaos" for c in cases)
    out = [
        "# Evaluation results",
        "",
        f"Generated by `centinela eval run` on {datetime.now(UTC):%Y-%m-%d %H:%M} UTC: {n} cases "
        f"({chaos} recorded in chaos runs on the cluster, {n - chaos} synthetic), replayed through "
        "the production prompt, validation and proposal guards. Temperature 0.",
        "",
        "| | rule-based baseline | " + " | ".join(labels) + " |",
        "|---|---|" + "---|" * len(labels),
    ]
    base = sums[labels[0]]["baseline_root_cause"] if labels else 0
    out.append(
        "| **root cause right** | "
        + _pct(int(base), n)
        + " | "
        + " | ".join(_pct(int(sums[lb]["root_cause"]), n) for lb in labels)
        + " |"
    )
    for key, title in (
        ("fully_right", "**cause AND action right** (valid answer)"),
        ("category", "category right"),
        ("right_action", "**right action reaches the human**"),
        ("harmful", "**wrong action proposed**"),
        ("right_action_v08", "right action, with only the v0.8 guards"),
        ("harmful_v08", "wrong action, with only the v0.8 guards"),
        ("invalid", "invalid answers (after one retry)"),
    ):
        out.append(
            f"| {title} | - | " + " | ".join(_pct(int(sums[lb][key]), n) for lb in labels) + " |"
        )
    out.append(
        "| median / max seconds per case | 0 | "
        + " | ".join(f"{sums[lb]['median_seconds']} / {sums[lb]['max_seconds']}" for lb in labels)
        + " |"
    )
    out.append(
        "| mean prompt tokens | - | "
        + " | ".join(str(sums[lb]["mean_prompt_tokens"]) for lb in labels)
        + " |"
    )
    out += [
        "",
        "Per case: blamed component / what would reach the human. ✓ = right, ✗ = wrong.",
        "",
        "| Case | Expected | Baseline | " + " | ".join(labels) + " |",
        "|---|---|---|" + "---|" * len(labels),
    ]
    by = {(s.config, s.case): s for s in scores}
    for case in cases:
        first = by.get((labels[0], case.name)) if labels else None
        base_cell = (
            f"{first.baseline} {'✓' if first.baseline_ok else '✗'}" if first is not None else "-"
        )
        cells = []
        for lb in labels:
            s = by.get((lb, case.name))
            if s is None:
                cells.append("-")
                continue
            mark = "✓" if s.root_ok else "✗"
            act = "✓" if s.action_ok else ("✗ **harmful**" if s.harmful else "✗")
            cells.append(f"{s.root_cause} {mark} / `{s.proposal}` {act}")
        expected = f"{'/'.join(case.root_cause)} · `{' or '.join(case.actions)}`"
        out.append(f"| `{case.name}` | {expected} | {base_cell} | " + " | ".join(cells) + " |")

    live = [c for c in cases if c.live]
    if live:
        out += [
            "",
            "## In the cluster (chaos runs)",
            "",
            "| Case | MTTD | detection → diagnosis | model time | live answer | proposed |",
            "|---|---|---|---|---|---|",
        ]
        for c in live:
            lv = c.live
            out.append(
                f"| `{c.name}` | {lv.get('mttd_seconds')} s | "
                f"{lv.get('detected_to_diagnosis_seconds')} s | {lv.get('model_seconds')} s | "
                f"{lv.get('root_cause')} ({lv.get('category')}) | "
                f"{', '.join(lv.get('proposals') or []) or 'none'} |"
            )
    return "\n".join(out) + "\n"


def write(
    scores: Sequence[Score], configs: Sequence[Config], cases: Sequence[Case], out_dir: Path
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "configs": [asdict(c) for c in configs],
        "summary": {c.label: summary(scores, c.label) for c in configs},
        "scores": [asdict(s) for s in scores],
    }
    json_path, md_path = out_dir / "results.json", out_dir / "results.md"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    md_path.write_text(markdown(scores, configs, cases))
    return json_path, md_path
