"""The public demo (Phase 10): a static page built from the repository's own evidence.

    uv run centinela site --out _site        (published to GitHub Pages by .github/workflows/pages.yml)

It replays real incidents recorded in chaos runs (what broke, what the AI saw, what it
concluded, what reached the human) and shows the evaluation and red-team results. It is
static HTML: no server, no JavaScript, no console, no credentials. Nobody on the internet can
run anything, which is the strongest way to keep a public demo safe. Every value that comes
from a log, an event or a model answer is HTML-escaped.
"""

import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from centinela import __version__
from centinela.agent import snapshot
from centinela.agent.llm import LlmReply
from centinela.eval.cases import Case, load
from centinela.eval.run import Config, score

REPO = "https://github.com/Fabricioanchundia/IA-CENTINELA"
CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1f23;--muted:#5d636d;--line:#e3e4e8;--accent:#0b6bcb;
--ok:#1a7f37;--bad:#c4321f;--warn:#9a6700;--code:#f1f2f4;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a20;--ink:#e8eaee;--muted:#9aa1ad;
--line:#2a2f38;--accent:#5aa9ff;--ok:#4ac26b;--bad:#ff7b72;--warn:#d29922;--code:#1f232b;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1040px;margin:0 auto;padding:32px 16px 64px}
a{color:var(--accent)}h1{font-size:2.1rem;margin:0 0 4px;letter-spacing:-.02em}
h2{font-size:1.35rem;margin:48px 0 12px;letter-spacing:-.01em}h3{font-size:1rem;margin:18px 0 6px}
.lead{color:var(--muted);font-size:1.1rem;margin:0 0 20px;max-width:760px}
.links a{margin-right:16px;font-weight:600;text-decoration:none}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.stat b{display:block;font-size:1.8rem;letter-spacing:-.02em}.stat span{color:var(--muted);font-size:.92rem}
.flow{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
.flow div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px;font-size:.92rem}
.flow i{color:var(--muted);font-style:normal}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:12px;overflow:hidden;font-size:.93rem}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}tr:last-child td{border-bottom:0}
.scroll{overflow-x:auto}.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad);font-weight:600}
.muted{color:var(--muted)}code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.85rem}
code{background:var(--code);padding:1px 5px;border-radius:5px}
pre{background:var(--code);padding:12px;border-radius:10px;overflow-x:auto;white-space:pre-wrap;
word-break:break-word;max-height:420px}
details{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:10px 0}
summary{cursor:pointer;padding:12px 16px;list-style:none;display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline}
summary::-webkit-details-marker{display:none}summary b{min-width:220px}
details>div{padding:0 16px 16px}.pill{font-size:.8rem;border:1px solid var(--line);border-radius:99px;padding:1px 8px}
footer{margin-top:56px;color:var(--muted);font-size:.9rem}
"""


def _mark(ok: bool) -> str:
    return '<span class="ok">✓</span>' if ok else '<span class="bad">✗</span>'


class _Recorded:
    """Gives back the answer the model gave live, to replay it through today's guards."""

    model = "recorded"

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer

    def chat_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LlmReply:
        return LlmReply(self.answer, self.model, 0, 0, 0.0)


def today(case: Case) -> str:
    """What the live answer would reach the human with the current guards."""
    lv = case.live
    ev = snapshot.from_json(case.evidence)
    cite = next((i.id for i in ev.items if lv.get("root_cause") in i.services), "E1")
    kind, _, target = str(lv.get("action", "none:none")).partition(":")
    answer = {
        "analysis": "",
        "root_cause_service": lv.get("root_cause"),
        "category": lv.get("category"),
        "symptoms": [],
        "evidence": [cite],
        "summary": lv.get("summary", ""),
        "confidence": 0.9,
        "recommended_action": kind,
        "action_target": target,
        "runbook": "none",
    }
    return score(case, Config("recorded", "recorded"), _Recorded(answer), 100_000).proposal


def _incident(case: Case) -> str:
    lv = case.live
    ev = snapshot.from_json(case.evidence)
    root_ok = lv.get("root_cause") in case.root_cause
    proposals = lv.get("proposals") or []
    proposed = ", ".join(proposals) or "none"
    action_ok = (proposals[0] if proposals else "none") in case.actions
    name = case.name.removeprefix("chaos-")
    current = today(case)
    now = ""
    if current != (proposals[0] if proposals else "none"):
        now = (
            f"<br>With today's guards the same answer gives <code>{escape(current)}</code> "
            f"{_mark(current in case.actions)} (a rule added after this run)."
        )
    return f"""
<details><summary><b>{escape(name)}</b>
<span class="pill">detected in {lv.get("mttd_seconds")} s</span>
<span class="pill">AI diagnosis {lv.get("detected_to_diagnosis_seconds")} s later</span>
<span>root cause <code>{escape(str(lv.get("root_cause")))}</code> {_mark(root_ok)}</span>
<span>proposed <code>{escape(proposed)}</code> {_mark(action_ok)}</span></summary><div>
<h3>What was broken</h3><p>{escape(case.fault)}</p>
<h3>What the AI concluded ({escape(str(lv.get("model")))}, {lv.get("model_seconds")} s)</h3>
<p>“{escape(str(lv.get("summary")))}”<br><span class="muted">root cause
<code>{escape(str(lv.get("root_cause")))}</code> · category <code>{escape(str(lv.get("category")))}</code>
· recommends <code>{escape(str(lv.get("action")))}</code> · rule-based baseline:
<code>{escape(str(lv.get("baseline")))}</code></span></p>
<h3>What reached the human</h3><p><code>{escape(proposed)}</code> — right answer:
<code>{escape(" or ".join(case.actions))}</code>. Nothing runs until a human approves it.{now}</p>
<h3>Exactly what the AI saw</h3><pre>{escape(ev.render())}</pre></div></details>"""


def _eval_table(results: dict[str, Any]) -> str:
    labels = [c["label"] for c in results["configs"]]
    s = results["summary"]
    first = s[labels[0]]
    n = first["cases"]
    rows = [
        ("root cause right", "root_cause", str(first["baseline_root_cause"])),
        ("right action reaches the human", "right_action", "–"),
        ("wrong action proposed", "harmful", "–"),
        ("median seconds per case", "median_seconds", "0"),
    ]
    head = "".join(f"<th>{escape(lb)}</th>" for lb in labels)
    body = ""
    for title, key, base in rows:
        cells = "".join(f"<td>{escape(str(s[lb][key]))}</td>" for lb in labels)
        body += f"<tr><td>{title}</td><td>{base}</td>{cells}</tr>"
    return (
        f'<div class="scroll"><table><tr><th>{n} cases</th><th>rule-based baseline</th>{head}</tr>'
        f"{body}</table></div>"
    )


def _redteam_table(files: list[Path]) -> str:
    body = ""
    for path in files:
        data = json.loads(path.read_text())
        scenario = data.get("scenario") or path.stem.split("-", 3)[-1]
        t = data["totals"]
        cells = "".join(
            f"<td>{t[k]['hijacked']}/{t[k]['attacks']}</td>" for k in ("L0", "L1", "L2")
        )
        body += f"<tr><td><code>{escape(scenario)}</code></td>{cells}</tr>"
    return (
        '<div class="scroll"><table><tr><th>incident</th><th>naive agent</th><th>v0.7 (prompt '
        f"defences)</th><th>v0.8+ (guards in code)</th></tr>{body}</table></div>"
    )


def build(root: Path) -> str:
    cases = [c for c in load(root / "eval" / "cases") if c.source == "chaos" and c.live]
    results = json.loads((root / "docs" / "eval" / "results.json").read_text())
    redteam = sorted((root / "docs" / "redteam").glob("results-*.json"))
    summary = results["summary"]
    default = summary.get("qwen3:8b") or next(iter(summary.values()))
    live_ok = sum(c.live.get("root_cause") in c.root_cause for c in cases)
    slow = next(
        (json.loads(p.read_text())["totals"] for p in redteam if "slow-orders" in p.name), None
    )
    injection = f"{slow['L1']['hijacked']} → {slow['L2']['hijacked']}" if slow else "–"
    built = datetime.now(UTC).strftime("%Y-%m-%d")
    incidents = "".join(_incident(c) for c in cases)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="referrer" content="no-referrer">
<title>Centinela · AI SRE agent</title>
<meta name="description" content="An AI SRE agent that detects incidents in a Kubernetes shop, diagnoses them with a local LLM and proposes fixes a human approves.">
<style>{CSS}</style></head><body><main>
<h1>Centinela</h1>
<p class="lead">An AI SRE agent that watches a microservices shop on Kubernetes, detects incidents,
diagnoses them with a <b>local</b> language model from metrics, logs and cluster state, and proposes
a fix that a <b>human approves</b> before a separate, least-privilege executor applies it.</p>
<p class="links"><a href="{REPO}">Source code</a><a href="{REPO}/tree/main/docs/adr">Design decisions</a>
<a href="{REPO}/blob/main/docs/eval/results.md">Evaluation</a><a href="{REPO}/releases">Releases</a></p>

<div class="grid">
<div class="card stat"><b>{live_ok}/{len(cases)}</b><span>real faults in the cluster diagnosed right</span></div>
<div class="card stat"><b>{default["right_action"]}/{default["cases"]}</b><span>right action reaches the human (offline evaluation)</span></div>
<div class="card stat"><b>{default["harmful"]}</b><span>wrong actions proposed by the default model</span></div>
<div class="card stat"><b>{injection}</b><span>prompt-injection attacks that got through, before → after the guards</span></div>
</div>

<h2>How it works</h2>
<div class="flow"><div>Prometheus alerts</div><i>→</i><div>incident (grouped)</div><i>→</i>
<div>evidence: metrics, logs, Kubernetes, runbooks, history</div><i>→</i><div>local LLM, JSON answer, validated</div>
<i>→</i><div>policy and guards in code</div><i>→</i><div><b>human approves</b></div><i>→</i>
<div>executor (RBAC: 4 deployments)</div><i>→</i><div>verified fix</div></div>
<p class="muted">Three identities: the agent can only read; the human has no cluster rights; the executor can
only scale or restart four named deployments, never the database.</p>

<h2>Incident replay</h2>
<p>Real faults injected by <code>scripts/chaos.sh</code> into the running shop. Open one to see exactly
what the AI was given and what it answered.</p>
{incidents}
<p class="muted">Times as measured live. catalog-down's detection time was inflated by a late latency
alert left over from the previous fault; since v0.9 a late alert reopens its own incident.</p>

<h2>Evaluation: {default["cases"]} replayable cases</h2>
<p>Recorded and synthetic incidents replayed through the production prompt, validation and guards
(<code>centinela eval run</code>).</p>
{_eval_table(results)}

<h2>Red team: prompt injection hidden in logs</h2>
<p>Attacks hidden in log lines, Kubernetes events and old postmortems; how many put the attacker's
action in front of the human (<code>centinela redteam</code>).</p>
{_redteam_table(redteam)}

<h2>Why this page is safe</h2>
<p>It is static HTML built from files in the repository: no server, no JavaScript, no approval
console, no credentials. The real console only listens inside the cluster. Every log line and
model answer shown here is escaped.</p>

<footer>Centinela v{escape(__version__)} · built {built} from the repository by GitHub Actions ·
<a href="{REPO}">{REPO.removeprefix("https://")}</a></footer>
</main></body></html>
"""


def write(root: Path, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(build(root))
    (out / ".nojekyll").write_text("")
    return page
