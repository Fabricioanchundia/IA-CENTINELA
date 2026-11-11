"""The public demo (Phase 10): a static page built from the repository's own evidence.

    uv run centinela site --out _site        (published to GitHub Pages by .github/workflows/pages.yml)

It replays real incidents recorded in chaos runs (what broke, what the AI saw, what it
concluded, what reached the human) and shows the evaluation and red-team results. It is
static HTML: no server, no JavaScript, no console, no credentials, no third-party requests
(the Geist fonts are served from the same site). Nobody on the internet can run anything,
which is the strongest way to keep a public demo safe. Every value that comes from a log, an
event or a model answer is HTML-escaped.
"""

import json
import shutil
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from centinela import __version__
from centinela.agent import snapshot
from centinela.agent.llm import LlmReply
from centinela.eval.cases import Case, load
from centinela.eval.run import Config, score

REPO = "https://github.com/Fabricioanchundia/IA-CENTINELA"
ASSETS = Path(__file__).parent / "site_assets"
CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; img-src data:; "
    "base-uri 'none'; form-action 'none'"
)
DEFAULT_MODEL = "qwen3:8b"

CSS = """
@font-face{font-family:Geist;src:url(geist.woff2) format("woff2");font-weight:100 900;font-display:swap}
@font-face{font-family:"Geist Mono";src:url(geist-mono.woff2) format("woff2");font-weight:100 900;font-display:swap}
:root{--paper:#f3f5f8;--surface:#fff;--ink:#101a27;--ink2:#38455a;--muted:#647185;--line:#d8dee6;
--steel:#2251c3;--ok:#1e7a47;--warn:#a15c07;--bad:#b42318;--term:#0f1824;--term-ink:#d5deea;
--term-dim:#7d8da3;--sans:Geist,ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif;
--mono:"Geist Mono",ui-monospace,"Cascadia Code","SF Mono",Menlo,Consolas,monospace;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--paper:#0b121c;--surface:#111b27;--ink:#e7edf4;--ink2:#c2ccd8;
--muted:#8c99ab;--line:#233142;--steel:#79a2ff;--ok:#4cc27e;--warn:#e6a23c;--bad:#ff7a6e;--term:#070d15;
color-scheme:dark}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:400 16px/1.6 var(--sans);
font-feature-settings:"ss01","tnum"}
a{color:var(--steel);text-underline-offset:3px}
a:focus-visible,summary:focus-visible{outline:2px solid var(--steel);outline-offset:3px;border-radius:4px}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px}
header.top{border-bottom:1px solid var(--line);background:var(--surface)}
header.top .wrap{display:flex;align-items:center;justify-content:space-between;gap:16px;height:60px}
.mark{font-weight:650;font-size:17px;letter-spacing:-.01em;color:var(--ink);text-decoration:none;display:flex;gap:10px;align-items:center}
.mark span{font:500 12px var(--mono);color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:1px 7px}
nav a{color:var(--ink2);text-decoration:none;font-size:14.5px;margin-left:22px}
nav a:hover{color:var(--steel)}
.hero{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.05fr);gap:56px;padding-top:72px;padding-bottom:64px;align-items:start}
h1{font-size:clamp(2.1rem,4.2vw,3.15rem);line-height:1.08;letter-spacing:-.035em;font-weight:650;margin:0 0 22px;max-width:15ch}
.lede{font-size:1.125rem;color:var(--ink2);margin:0 0 32px;max-width:52ch}
.facts{display:grid;grid-template-columns:repeat(3,auto);gap:4px 36px;margin:0;justify-content:start}
.facts dt{order:2;color:var(--muted);font-size:13.5px;line-height:1.35;max-width:17ch}
.facts dd{order:1;margin:0;font:600 1.75rem/1.1 var(--sans);letter-spacing:-.03em}
.facts div{display:flex;flex-direction:column;gap:6px}
.timeline{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:22px 24px 8px}
.timeline header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:14px}
.timeline header b{font-weight:600;white-space:nowrap}.timeline header span{color:var(--muted);font-size:13.5px}
.timeline ol{list-style:none;margin:0;padding:0}
.timeline li{display:grid;grid-template-columns:76px 14px 1fr;gap:12px;padding:0 0 18px;position:relative}
.timeline time{font:500 13px/1.6 var(--mono);color:var(--muted)}
.timeline i{width:9px;height:9px;border-radius:50%;margin-top:7px;background:var(--line);position:relative;z-index:1;justify-self:center}
.timeline li:not(:last-child)::after{content:"";position:absolute;left:94px;top:16px;bottom:0;width:1px;background:var(--line)}
.timeline i.warn{background:var(--warn)}.timeline i.bad{background:var(--bad)}.timeline i.steel{background:var(--steel)}
.timeline i.wait{background:var(--surface);border:2px solid var(--steel)}
.timeline p{margin:0;color:var(--ink2);font-size:14.5px;line-height:1.5}.timeline p b{color:var(--ink);font-weight:600}
.timeline q{display:block;margin-top:4px;color:var(--ink);font-style:normal}
section{padding:56px 0;border-top:1px solid var(--line)}
h2{font-size:1.6rem;letter-spacing:-.025em;font-weight:620;margin:0 0 10px}
.intro{color:var(--ink2);max-width:68ch;margin:0 0 28px}
.diagram{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px}
.diagram svg{display:block;min-width:820px;width:100%;height:auto}
.zone{fill:none;stroke:var(--line);stroke-width:1.5;stroke-dasharray:5 4}
.box{fill:var(--paper);stroke:var(--line)}.box.key{stroke:var(--steel);stroke-width:1.5}
.zl{font:600 12.5px var(--sans);fill:var(--muted)}.bl{font:500 13.5px var(--sans);fill:var(--ink)}
.bs{font:400 11.5px var(--mono);fill:var(--muted)}.al{font:400 11.5px var(--sans);fill:var(--muted)}
.arrow{stroke:var(--muted);stroke-width:1.4;fill:none;marker-end:url(#tip)}.arrow.act{stroke:var(--steel)}
.tip{fill:var(--muted)}.tip.act{fill:var(--steel)}
.idents{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px;margin-top:28px}
.idents h3{font-size:15px;margin:0 0 4px;font-weight:600}.idents p{margin:0;color:var(--ink2);font-size:14.5px}
.runs{border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--surface)}
.runs .head,.runs summary{display:grid;grid-template-columns:1.35fr .8fr .8fr 1fr 1.25fr .55fr;gap:14px;padding:13px 20px;align-items:center}
.runs .head{font-size:13px;color:var(--muted);border-bottom:1px solid var(--line)}
.runs details{border-bottom:1px solid var(--line)}.runs details:last-child{border-bottom:0}
.runs summary{cursor:pointer;list-style:none;font-size:14.5px}.runs summary::-webkit-details-marker{display:none}
.runs summary:hover{background:color-mix(in srgb,var(--steel) 5%,transparent)}
.runs summary b{font-weight:600}.runs summary b::before{content:"+";display:inline-block;width:16px;color:var(--muted);font-weight:400}
.runs details[open] summary b::before{content:"\\2212"}
.num{font:500 13.5px var(--mono);color:var(--ink2)}
code{font:500 13px var(--mono);background:color-mix(in srgb,var(--steel) 9%,transparent);padding:2px 6px;border-radius:5px}
.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad);font-weight:600}
.detail{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(0,1fr);gap:24px;padding:4px 20px 24px}
.term{background:var(--term);color:var(--term-ink);border-radius:10px;padding:16px 18px;font:400 12.5px/1.6 var(--mono);
white-space:pre-wrap;word-break:break-word;max-height:460px;overflow:auto;margin:0}
.term::before{content:attr(data-title);display:block;color:var(--term-dim);margin-bottom:10px}
.answer h3{font-size:14px;margin:0 0 10px;font-weight:600}
.answer dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:8px 16px;font-size:14px}
.answer dt{color:var(--muted)}.answer dd{margin:0;color:var(--ink)}
.answer blockquote{margin:16px 0 0;padding:12px 14px;border-left:3px solid var(--steel);background:var(--paper);border-radius:0 8px 8px 0;font-size:14.5px}
.note{color:var(--muted);font-size:14px;margin:14px 0 0;max-width:80ch}
table{width:100%;border-collapse:collapse;background:var(--surface);font-size:14.5px}
.tablebox{border:1px solid var(--line);border-radius:14px;overflow-x:auto}
th,td{text-align:left;padding:12px 16px;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-weight:500;color:var(--muted);font-size:13px;white-space:normal;vertical-align:bottom}tr:last-child td{border-bottom:0}
tr.chosen td{background:color-mix(in srgb,var(--steel) 6%,transparent)}
.bar{display:flex;align-items:center;gap:10px}.bar s{display:block;height:6px;border-radius:3px;background:var(--steel);text-decoration:none}
.bar s.base{background:var(--muted)}
.tag{font-size:12px;color:var(--steel);border:1px solid var(--steel);border-radius:999px;padding:0 7px;margin-left:8px}
.two{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:48px}
.controls{list-style:none;padding:0;margin:0;display:grid;gap:14px}
.controls li{padding-left:18px;position:relative;color:var(--ink2);font-size:15px}
.controls li::before{content:"";position:absolute;left:0;top:10px;width:7px;height:7px;border-radius:2px;background:var(--steel)}
.controls b{color:var(--ink);font-weight:600}
footer{border-top:1px solid var(--line);padding:28px 0 48px;color:var(--muted);font-size:14px}
footer .wrap{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
@media (max-width:880px){.hero{grid-template-columns:minmax(0,1fr);gap:40px;padding-top:48px;padding-bottom:48px}.two,.idents{grid-template-columns:minmax(0,1fr)}
.detail{grid-template-columns:1fr}.runs .head{display:none}
.runs summary{display:flex;flex-wrap:wrap;gap:6px 10px;padding:14px 16px}.runs summary b{flex-basis:100%}.runs summary .num{display:none}.detail{padding:4px 16px 20px}
nav a{margin-left:14px}.facts{grid-template-columns:repeat(3,minmax(0,1fr));gap:4px 18px}}
@media (max-width:520px){nav a:nth-child(n+3){display:none}.wrap{padding-left:16px;padding-right:16px}}
"""


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


def _e(value: object) -> str:
    return escape(str(value))


def _seconds(value: object) -> str:
    try:
        return f"{float(str(value)):.0f} s"
    except ValueError:
        return "-"


def _verdict(ok: bool) -> str:
    return '<span class="ok">correct</span>' if ok else '<span class="bad">wrong</span>'


def _first_proposal(case: Case) -> str:
    proposals = case.live.get("proposals") or []
    return str(proposals[0]) if proposals else "none"


def _hero_timeline(case: Case) -> str:
    """The most characteristic thing in this world: one real incident, minute by minute."""
    lv = case.live
    ev = snapshot.from_json(case.evidence)
    first = min(ev.alerts, key=lambda a: a.active_at)
    detected = ev.incident.detected_at
    diagnosed = detected + timedelta(seconds=float(lv.get("detected_to_diagnosis_seconds", 0)))
    proposal = _first_proposal(case)
    kind, _, target = proposal.partition(":")
    action = "scale it back to 1 replica" if kind == "scale_up" else f"{kind} it"

    def t(at: datetime) -> str:
        return f'<time datetime="{at.isoformat()}">{at:%H:%M:%S}</time>'

    steps = [
        (
            t(first.active_at),
            "warn",
            f"<b>{_e(first.name)}</b> on {_e(first.service)}: {_e(first.summary)}",
        ),
        (
            t(detected),
            "bad",
            f"<b>Incident {_e(ev.incident.id)} opened</b>, {_seconds(lv.get('mttd_seconds'))} "
            "after the first signal; related alerts grouped into it",
        ),
        (
            t(diagnosed),
            "steel",
            f"<b>Diagnosis</b> from {_e(lv.get('model'))} in {_seconds(lv.get('model_seconds'))}, "
            f"citing the evidence<q>{_e(lv.get('summary'))}</q>",
        ),
        (
            t(diagnosed),
            "wait",
            f"<b>Proposed: {_e(target)}, {_e(action)}.</b> Nothing runs until an engineer "
            "approves it",
        ),
    ]
    items = "".join(
        f'<li>{when}<i class="{cls}"></i><p>{text}</p></li>' for when, cls, text in steps
    )
    return (
        f'<div class="timeline"><header><b>{_e(case.name.removeprefix("chaos-"))}</b>'
        f"<span>recorded {ev.incident.started_at:%Y-%m-%d}, times in UTC</span></header>"
        f"<ol>{items}</ol></div>"
    )


ARCHITECTURE = """
<svg viewBox="0 0 960 372" role="img" aria-labelledby="arch-title">
<title id="arch-title">Centinela architecture: the shop is observed, the agent diagnoses with a
local model, a human approves, a separate executor applies the fix.</title>
<defs><marker id="tip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path class="tip" d="M0 0L10 5L0 10z"/></marker></defs>
<rect class="zone" x="10" y="20" width="230" height="300" rx="12"/><text class="zl" x="26" y="44">shop namespace</text>
<rect class="box" x="30" y="60" width="190" height="38" rx="8"/><text class="bl" x="46" y="84">catalog</text>
<rect class="box" x="30" y="108" width="190" height="38" rx="8"/><text class="bl" x="46" y="132">orders</text>
<rect class="box" x="30" y="156" width="190" height="38" rx="8"/><text class="bl" x="46" y="180">payments</text>
<rect class="box" x="30" y="204" width="190" height="38" rx="8"/><text class="bl" x="46" y="228">redis</text>
<rect class="box" x="30" y="252" width="190" height="38" rx="8"/><text class="bl" x="46" y="276">postgres</text>
<rect class="zone" x="268" y="20" width="180" height="170" rx="12"/><text class="zl" x="284" y="44">observability</text>
<rect class="box" x="286" y="60" width="144" height="46" rx="8"/><text class="bl" x="300" y="80">Prometheus</text><text class="bs" x="300" y="97">alerts, metrics</text>
<rect class="box" x="286" y="118" width="144" height="46" rx="8"/><text class="bl" x="300" y="138">Loki</text><text class="bs" x="300" y="155">logs</text>
<rect class="zone" x="476" y="20" width="250" height="300" rx="12"/><text class="zl" x="492" y="44">centinela namespace</text>
<rect class="box" x="494" y="60" width="214" height="46" rx="8"/><text class="bl" x="508" y="80">collector</text><text class="bs" x="508" y="97">alerts to incidents</text>
<rect class="box key" x="494" y="128" width="214" height="46" rx="8"/><text class="bl" x="508" y="148">agent</text><text class="bs" x="508" y="165">read-only account</text>
<rect class="box" x="494" y="250" width="214" height="52" rx="8"/><text class="bl" x="508" y="271">executor</text><text class="bs" x="508" y="289">scale, restart: 4 deployments</text>
<rect class="zone" x="754" y="20" width="196" height="300" rx="12"/><text class="zl" x="770" y="44">outside the cluster</text>
<rect class="box" x="772" y="60" width="160" height="46" rx="8"/><text class="bl" x="786" y="80">local LLM</text><text class="bs" x="786" y="97">Ollama, own GPU</text>
<rect class="box key" x="772" y="186" width="160" height="46" rx="8"/><text class="bl" x="786" y="206">engineer</text><text class="bs" x="786" y="223">console or CLI</text>
<path class="arrow" d="M220 127 H268 V90 H282"/><text class="al" x="228" y="120">metrics</text>
<path class="arrow" d="M244 175 H262 V141 H282"/><text class="al" x="228" y="192">logs</text>
<path class="arrow" d="M430 83 H490"/>
<path class="arrow" d="M601 106 V124"/>
<path class="arrow" d="M708 151 H740 V83 H768"/><text class="al" x="714" y="72">evidence</text>
<path class="arrow" d="M708 162 H740 V209 H768"/><text class="al" x="644" y="224">proposal</text>
<path class="arrow act" style="marker-end:none" d="M852 232 V276 H712"/><path class="tip act" d="M712 276 l9 -4.5 v9z"/><text class="al" x="770" y="268">approves</text>
<path class="arrow act" style="marker-end:none" d="M494 276 H470 V350 H125 V294"/><path class="tip act" d="M125 291 l-4.5 9 h9z"/><text class="al" x="250" y="343">scales or restarts, then verifies</text>
</svg>
"""


def _run_row(case: Case) -> str:
    lv = case.live
    ev = snapshot.from_json(case.evidence)
    root_ok = lv.get("root_cause") in case.root_cause
    proposed = _first_proposal(case)
    action_ok = proposed in case.actions
    current = today(case)
    now = ""
    if current != proposed:
        now = (
            f"<dt>Today</dt><dd>With today's guards the same answer gives <code>{_e(current)}</code> "
            f"({'correct' if current in case.actions else 'still wrong'}): a rule added after this run.</dd>"
        )
    return f"""<details><summary><b>{_e(case.name.removeprefix("chaos-"))}</b>
<span class="num">{_seconds(lv.get("mttd_seconds"))}</span>
<span class="num">{_seconds(lv.get("detected_to_diagnosis_seconds"))}</span>
<span><code>{_e(lv.get("root_cause"))}</code></span>
<span><code>{_e(proposed)}</code></span>
<span>{_verdict(root_ok and action_ok)}</span></summary>
<div class="detail"><pre class="term" data-title="Exactly what the AI saw">{_e(ev.render())}</pre>
<div class="answer"><h3>What happened</h3><dl>
<dt>Fault</dt><dd>{_e(case.fault)}</dd>
<dt>Detected after</dt><dd>{_seconds(lv.get("mttd_seconds"))}; diagnosis {_seconds(lv.get("detected_to_diagnosis_seconds"))} later</dd>
<dt>Root cause</dt><dd><code>{_e(lv.get("root_cause"))}</code> {_verdict(root_ok)}</dd>
<dt>Category</dt><dd><code>{_e(lv.get("category"))}</code></dd>
<dt>Recommends</dt><dd><code>{_e(lv.get("action"))}</code></dd>
<dt>To the human</dt><dd><code>{_e(proposed)}</code> {_verdict(action_ok)}; right answer
<code>{_e(" or ".join(case.actions))}</code></dd>{now}
<dt>Rule-based</dt><dd><code>{_e(lv.get("baseline"))}</code></dd>
<dt>Model</dt><dd>{_e(lv.get("model"))}, {_seconds(lv.get("model_seconds"))}</dd></dl>
<blockquote>{_e(lv.get("summary"))}</blockquote></div></div></details>"""


def _eval_table(results: dict[str, Any]) -> str:
    labels = [c["label"] for c in results["configs"]]
    s = results["summary"]
    n = int(s[labels[0]]["cases"])

    def bar(value: int, cls: str = "") -> str:
        return f'<span class="bar"><s class="{cls}" style="width:{round(90 * value / n)}px"></s>{value}/{n}</span>'

    rows = [
        "<tr><td>rule-based baseline</td>"
        f"<td>{bar(int(s[labels[0]]['baseline_root_cause']), 'base')}</td><td>not measured</td><td>-</td><td>instant</td></tr>"
    ]
    for label in labels:
        m = s[label]
        chosen = label == DEFAULT_MODEL
        tag = '<span class="tag">in use</span>' if chosen else ""
        rows.append(
            f'<tr class="{"chosen" if chosen else ""}"><td>{_e(label)}{tag}</td>'
            f"<td>{bar(int(m['root_cause']))}</td><td>{m['right_action']}/{n}</td>"
            f"<td>{m['harmful']}</td><td>{m['median_seconds']} s</td></tr>"
        )
    return (
        '<div class="tablebox"><table><thead><tr><th>Configuration</th><th>Root cause right</th>'
        "<th>Right action reaches the human</th><th>Wrong actions proposed</th>"
        f"<th>Median time</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _redteam_table(files: list[Path]) -> str:
    body = ""
    for path in files:
        data = json.loads(path.read_text())
        scenario = data.get("scenario") or path.stem.split("-", 3)[-1]
        t = data["totals"]
        cells = "".join(
            f"<td>{t[k]['hijacked']} of {t[k]['attacks']}</td>" for k in ("L0", "L1", "L2")
        )
        body += f"<tr><td><code>{_e(scenario)}</code></td>{cells}</tr>"
    return (
        '<div class="tablebox"><table><thead><tr><th>Incident</th><th>No defences</th>'
        "<th>Prompt defences only</th><th>Guards in code</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def build(root: Path) -> str:
    cases = [c for c in load(root / "eval" / "cases") if c.source == "chaos" and c.live]
    results = json.loads((root / "docs" / "eval" / "results.json").read_text())
    redteam = sorted((root / "docs" / "redteam").glob("results-*.json"))
    summary = results["summary"]
    default = summary.get(DEFAULT_MODEL) or next(iter(summary.values()))
    live_ok = sum(c.live.get("root_cause") in c.root_cause for c in cases)
    slow = next(
        (json.loads(p.read_text())["totals"] for p in redteam if "slow-orders" in p.name), None
    )
    hero_case = next((c for c in cases if c.name == "chaos-payments-down"), cases[0])
    injection = (
        (
            str(slow["L2"]["hijacked"]),
            f"injection attacks got through, down from {slow['L1']['hijacked']} with prompt defences",
        )
        if slow
        else ("-", "injection attacks")
    )
    rows = "".join(_run_row(c) for c in cases)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="referrer" content="no-referrer">
<title>Centinela: an AI SRE agent with a human in the loop</title>
<meta name="description" content="Centinela detects incidents in a Kubernetes shop, diagnoses them with a local language model and proposes fixes that an engineer approves.">
<link rel="preload" href="geist.woff2" as="font" type="font/woff2" crossorigin>
<style>{CSS}</style></head><body>
<header class="top"><div class="wrap"><a class="mark" href="{REPO}">Centinela<span>v{_e(__version__)}</span></a>
<nav><a href="#replay">Incidents</a><a href="#evaluation">Evaluation</a><a href="{REPO}/tree/main/docs/adr">Design decisions</a><a href="{REPO}">Source</a></nav></div></header>
<main>
<div class="wrap hero"><div>
<h1>Finds the cause of an outage and proposes the fix. An engineer decides.</h1>
<p class="lede">Centinela watches a microservices shop on Kubernetes. When alerts fire it opens an incident,
gathers metrics, logs, cluster state and runbooks, asks a language model running on local hardware for a
diagnosis, checks the answer against the evidence, and proposes one safe action. A separate executor with
the least possible permissions applies it only after a person approves.</p>
<dl class="facts">
<div><dt>real faults in the cluster diagnosed correctly</dt><dd>{live_ok} of {len(cases)}</dd></div>
<div><dt>wrong actions proposed across {default["cases"]} evaluation cases</dt><dd>{default["harmful"]}</dd></div>
<div><dt>{_e(injection[1])}</dt><dd>{_e(injection[0])}</dd></div>
</dl></div>
{_hero_timeline(hero_case)}
</div>

<section id="architecture"><div class="wrap">
<h2>How it is built</h2>
<p class="intro">Every arrow that changes something goes through a person. The model never holds credentials
and cannot reach the cluster API; the component that can change the cluster cannot think.</p>
<div class="diagram">{ARCHITECTURE}</div>
<div class="idents">
<div><h3>Agent</h3><p>Reads metrics, logs and Kubernetes state. Proposes, never acts. Untrusted text is
quarantined before the model sees it.</p></div>
<div><h3>Engineer</h3><p>Approves or rejects in a console with no cluster rights, or from the CLI. Proposals
expire after 15 minutes.</p></div>
<div><h3>Executor</h3><p>May scale or restart four named deployments, never the database. Re-checks the policy
and verifies the fix.</p></div>
</div></div></section>

<section id="replay"><div class="wrap">
<h2>Incidents from a chaos run</h2>
<p class="intro">Ten faults were injected into the running shop by <code>scripts/chaos.sh</code>; eight of them
should open an incident and did. Open a row to see the exact evidence the model received and what it
answered.</p>
<div class="runs"><div class="head"><span>Fault</span><span>Detected after</span><span>Diagnosis after</span>
<span>Root cause</span><span>Proposed to the engineer</span><span>Result</span></div>{rows}</div>
<p class="note">Times as measured during the run. The catalog-down detection time was inflated by a late
latency alert left over from the previous fault; since v0.9 a late alert reopens its own incident.</p>
</div></section>

<section id="evaluation"><div class="wrap">
<h2>Evaluation on {default["cases"]} replayable incidents</h2>
<p class="intro">Recorded and synthetic incidents replayed through the production prompt, validation and
guards with <code>centinela eval run</code>. A wrong action is a fix proposed to the engineer that would not
help or would cause harm.</p>
{_eval_table(results)}
</div></section>

<section id="security"><div class="wrap two"><div>
<h2>Prompt injection in logs</h2>
<p class="intro">Log lines quote what customers type, so anyone can write instructions into the agent's
evidence. Thirteen attacks, hidden in logs, Kubernetes events and old postmortems, measured with
<code>centinela redteam</code>: how many put the attacker's action in front of the engineer.</p>
{_redteam_table(redteam)}
</div><div>
<h2>Why this page is safe to publish</h2>
<ul class="controls">
<li><b>Static.</b> No server, no JavaScript, no forms. A content security policy blocks scripts even if one were injected.</li>
<li><b>Escaped.</b> Every log line and model answer shown here is treated as text.</li>
<li><b>Private by default.</b> The real approval console only listens inside the cluster.</li>
<li><b>Built in the open.</b> Generated by GitHub Actions from files in the repository; container images are signed and scanned.</li>
</ul></div></div></section>
</main>
<footer><div class="wrap"><span>Centinela v{_e(__version__)}, generated from the repository</span>
<a href="{REPO}">{REPO.removeprefix("https://")}</a></div></footer>
</body></html>
"""


def write(root: Path, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(build(root))
    for font in ("geist.woff2", "geist-mono.woff2", "OFL.txt"):
        shutil.copyfile(ASSETS / font, out / font)
    (out / ".nojekyll").write_text("")
    return page
