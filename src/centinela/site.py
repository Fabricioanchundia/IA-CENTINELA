"""The public demo (Phase 10): a page built from the repository's own evidence.

    uv run centinela site --out _site        (published to GitHub Pages by .github/workflows/pages.yml)

It opens with an incident player: real incidents recorded in chaos runs and red-team tests, told
step by step in Spanish or English for people who have never heard of SRE (site_player.py).
Below, for engineers: the architecture, how to run it, the evaluation, the red-team results and
every recorded incident with exactly what the AI saw.

There is no server and nothing to log into. The only script is player.js from the same site: it
switches frames and language and has no network access (the Content-Security-Policy allows no
connections, no third-party scripts and no inline scripts). Without it the page still shows every
step. Every value that comes from a log, an event or a model answer is HTML-escaped.
"""

import json
import shutil
from html import escape
from pathlib import Path
from typing import Any

from centinela import __version__
from centinela.agent import snapshot
from centinela.agent.llm import LlmReply
from centinela.eval.cases import Case, load
from centinela.eval.run import Config, score
from centinela.site_i18n import bilingual, bilingual_svg, fault_es, summary_es
from centinela.site_player import render, scenarios, t

REPO = "https://github.com/Fabricioanchundia/IA-CENTINELA"
ASSETS = Path(__file__).parent / "site_assets"
STATIC = ("geist.woff2", "geist-mono.woff2", "OFL.txt", "player.js")
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; font-src 'self'; "
    "img-src data:; base-uri 'none'; form-action 'none'"
)
DEFAULT_MODEL = "qwen3:8b"

CSS = """
@font-face{font-family:Geist;src:url(geist.woff2) format("woff2");font-weight:100 900;font-display:swap}
@font-face{font-family:"Geist Mono";src:url(geist-mono.woff2) format("woff2");font-weight:100 900;font-display:swap}
:root{--paper:#f3f5f8;--surface:#fff;--ink:#101a27;--ink2:#38455a;--muted:#647185;--line:#d8dee6;
--steel:#2251c3;--steel-ink:#fff;--ok:#1e7a47;--warn:#a15c07;--bad:#b42318;--term:#0f1824;--term-ink:#d5deea;
--term-dim:#7d8da3;--sans:Geist,ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif;
--mono:"Geist Mono",ui-monospace,"Cascadia Code","SF Mono",Menlo,Consolas,monospace;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--paper:#0b121c;--surface:#111b27;--ink:#e7edf4;--ink2:#c2ccd8;
--muted:#8c99ab;--line:#233142;--steel:#79a2ff;--steel-ink:#0b121c;--ok:#4cc27e;--warn:#e6a23c;--bad:#ff7a6e;--term:#070d15;
color-scheme:dark}}
html[data-lang="es"] .en,html:not([data-lang="es"]) .es{display:none}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{animation:none!important;transition:none!important}}
body{margin:0;background:var(--paper);color:var(--ink);font:400 16px/1.6 var(--sans);font-feature-settings:"ss01","tnum"}
a{color:var(--steel);text-underline-offset:3px}
a:focus-visible,summary:focus-visible,button:focus-visible{outline:2px solid var(--steel);outline-offset:3px;border-radius:6px}
button{font:inherit;color:inherit;cursor:pointer}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}
header.top{border-bottom:1px solid var(--line);background:var(--surface);position:sticky;top:0;z-index:5}
header.top .wrap{display:flex;align-items:center;justify-content:space-between;gap:16px;height:60px}
.mark{font-weight:650;font-size:17px;letter-spacing:-.01em;color:var(--ink);text-decoration:none;display:flex;gap:10px;align-items:center}
.mark span{font:500 12px var(--mono);color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:1px 7px}
.right{display:flex;align-items:center;gap:22px}
nav a{color:var(--ink2);text-decoration:none;font-size:14.5px;margin-left:20px}
nav a:hover{color:var(--steel)}
.lang{display:none;border:1px solid var(--line);border-radius:8px;padding:2px}
.js .lang{display:flex}
.lang button{border:0;background:none;padding:3px 9px;border-radius:6px;font-size:13px;font-weight:600;color:var(--muted)}
.lang button[aria-pressed="true"]{background:var(--ink);color:var(--paper)}
.hero{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:56px;align-items:end;padding-top:64px;padding-bottom:36px}
h1{font-size:clamp(2rem,3.9vw,3rem);line-height:1.1;letter-spacing:-.035em;font-weight:650;margin:0}
.lede{font-size:1.1rem;color:var(--ink2);margin:0 0 20px}
.links{display:flex;gap:18px;flex-wrap:wrap;font-size:15px;font-weight:500}
section{padding:56px 0;border-top:1px solid var(--line)}
section.demo{padding-top:8px;border-top:0}
h2{font-size:1.6rem;letter-spacing:-.025em;font-weight:620;margin:0 0 10px}
.intro{color:var(--ink2);max-width:70ch;margin:0 0 28px}
/* ---------------- the player ---------------- */
.player{background:var(--surface);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.pick{margin:0;padding:14px 20px 0;font-size:13.5px;font-weight:600;color:var(--muted)}
html:not(.js) .pick{display:none}
.tabs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-bottom:1px solid var(--line)}
html:not(.js) .tabs{display:none}
.tabs button{background:none;border:0;border-right:1px solid var(--line);text-align:left;padding:16px 20px 14px;
display:flex;flex-direction:column;gap:2px;position:relative;color:var(--ink2)}
.tabs button:last-child{border-right:0}
.tabs button b{font-weight:600;font-size:15px;color:var(--ink)}
.tabs button small{font-size:13px;color:var(--muted);line-height:1.4}
.tabs button[aria-selected="true"]{background:color-mix(in srgb,var(--steel) 7%,transparent)}
.tabs button[aria-selected="true"]::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:2px;background:var(--steel)}
.tabs button:hover b{color:var(--steel)}
.scenario{padding:28px 28px 20px}
.choices{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px}
html:not(.js) .choices,html:not(.js) .btns>button{pointer-events:none}
.choice{border:1.5px solid var(--steel);background:var(--surface);color:var(--steel);border-radius:10px;padding:9px 16px;font-size:15px;font-weight:600}
.choice:hover{background:var(--steel);color:var(--steel-ink)}
.btns>button.choice{padding:7px 0;font-size:14px}
.btns>button.approve:hover{filter:brightness(1.1)}
.levels{width:100%;border-collapse:collapse;margin:12px 0 10px;background:none;font-size:14px}
.levels th,.levels td{padding:8px 10px;border-bottom:1px solid var(--line);white-space:normal;text-align:left;vertical-align:top}
.levels thead th{font-size:12.5px;padding-top:0}
.levels tbody th{font-weight:500;color:var(--ink2);font-size:13.5px}
.levels tr.mine th,.levels tr.mine td{background:color-mix(in srgb,var(--ok) 9%,transparent)}
.why{font-size:14px!important}
html:not(.js) .scenario+.scenario{border-top:1px solid var(--line)}
.frames{list-style:none;margin:0;padding:0}
html:not(.js) .frame+.frame{margin-top:36px;padding-top:28px;border-top:1px dashed var(--line)}
.js .frame{display:none}
.js .frame.on{display:block;animation:enter .35s ease-out}
@keyframes enter{from{opacity:.25}to{opacity:1}}
.cap{max-width:78ch;margin-bottom:22px}
.meta{display:flex;gap:14px;margin:0 0 6px;font:500 13px var(--mono);color:var(--muted)}
.cap h3{font-size:1.55rem;line-height:1.25;letter-spacing:-.025em;font-weight:620;margin:0 0 6px;display:flex;align-items:flex-start;gap:12px}
.cap h3::before{content:"";width:10px;height:10px;border-radius:50%;background:var(--steel);flex:none;margin-top:.5em}
.frame.tone-bad .cap h3::before{background:var(--bad)}.frame.tone-good .cap h3::before{background:var(--ok)}
.cap>p:not(.meta){margin:0;color:var(--ink2);font-size:1.05rem}
.stage{display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.5fr) minmax(0,.9fr);gap:16px;align-items:stretch}
.pane{border:1px solid var(--line);border-radius:12px;padding:16px 18px;background:var(--paper);min-height:250px}
.pane-h{margin:0 0 12px;font-size:13px;font-weight:500;color:var(--muted)}
.pane h4:not(.pane-h){margin:0 0 8px;font-size:14px;font-weight:600}
.pane p{margin:0 0 8px;font-size:14.5px;color:var(--ink2)}
.quiet{color:var(--muted)!important}
.shop{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.shop li{display:grid;grid-template-columns:12px 1fr auto;gap:10px;align-items:center;font-size:14.5px;
background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.shop i{width:10px;height:10px;border-radius:50%;background:var(--ok)}
.shop .state{font-size:13px;color:var(--muted)}
.shop .s-slow i,.shop .s-errors i{background:var(--warn)}.shop .s-slow .state,.shop .s-errors .state{color:var(--warn);font-weight:600}
.shop .s-down i{background:var(--bad)}.shop .s-down .state{color:var(--bad);font-weight:600}
.shop .s-down{border-color:color-mix(in srgb,var(--bad) 45%,var(--line))}
.shop .s-fixed i{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent)}.shop .s-fixed .state{color:var(--ok);font-weight:600}
.alerts,.evidence{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.alerts li{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;background:var(--surface);border:1px solid var(--line);
border-left:3px solid var(--bad);border-radius:8px;padding:8px 12px;font-size:14px}
.alerts time{font:500 12.5px var(--mono);color:var(--muted);grid-row:span 2}
.alerts b{font-weight:600}.alerts span{color:var(--ink2)}
.evidence li{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:7px 12px;display:grid;gap:2px}
.evidence li.key{border-left:3px solid var(--steel)}
.evidence .kind{font-size:12.5px;color:var(--muted)}
.evidence code,.pane code.line{background:none;padding:0;font-size:12.5px;color:var(--ink);white-space:pre-wrap;word-break:break-word}
.raw{margin-top:10px;font-size:14px}.raw summary{cursor:pointer;color:var(--steel);font-weight:500}
.raw .term{margin-top:10px;max-height:320px}
.term.short{max-height:none;font-size:12px}
.said{margin:0}.said blockquote{margin:0 0 10px;padding:14px 16px;background:var(--surface);border:1px solid var(--line);
border-left:3px solid var(--steel);border-radius:8px;font-size:15.5px;line-height:1.5;color:var(--ink)}
.said figcaption{font-size:13px;color:var(--muted)}
.struck{text-decoration:line-through;text-decoration-color:var(--bad);color:var(--muted)!important}
.ok-line{color:var(--ok)!important;font-weight:600}
.score{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:0 0 12px}
.score div{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column-reverse;gap:2px}
.score dt{font-size:12.5px;color:var(--muted);line-height:1.35}.score dd{margin:0;font:600 1.9rem/1.1 var(--sans);color:var(--bad)}
.score .good{border-color:var(--ok)}.score .good dd{color:var(--ok)}
.phone{border-radius:24px;background:var(--surface)}
.phone .pane-h{text-align:center}
.msg{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:10px 12px;font-size:14px}
.msg b{font-size:12.5px;color:var(--muted);font-weight:600}.msg p{margin:2px 0 0!important;color:var(--ink)!important;font-size:14px!important}
.msg.warn{border-color:var(--warn)}.msg.good{border-color:var(--ok)}
.card{border:1px solid var(--steel);border-radius:14px;padding:12px;display:grid;gap:8px;background:var(--paper)}
.card small{font-size:12.5px;color:var(--steel);font-weight:600}
.card>b{font-size:15.5px;line-height:1.35}
.card>p{font-size:12.5px!important;color:var(--muted)!important;margin:0!important}
.btns{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.btns>span,.btns>button{text-align:center;border-radius:9px;padding:7px 0;font-size:14px;font-weight:600;border:1px solid var(--line)}
.btns .approve{background:var(--steel);color:var(--steel-ink);border-color:var(--steel)}
.card.approved{border-color:var(--ok)}.card.approved small{color:var(--ok)}
.card.approved .approve{background:var(--ok);border-color:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent)}
.card.approved .reject{opacity:.4}
.card.hijacked,.card.wrong{border-color:var(--bad)}.card.hijacked small,.card.wrong small{color:var(--bad)}
.controls{display:none;align-items:center;gap:12px;margin:0 0 24px;padding-bottom:18px;border-bottom:1px solid var(--line)}
.js .controls{display:flex}
.controls button:not(.dot){border:1px solid var(--line);background:var(--surface);border-radius:9px;padding:7px 14px;font-size:14.5px;font-weight:500}
.controls button.primary{background:var(--steel);color:var(--steel-ink);border-color:var(--steel)}
.controls button:disabled{opacity:.4;cursor:default}
.controls .play .busy,.playing .controls .play .idle{display:none}
.controls .again,.finished .controls .next,.waiting .controls .next,.finished .controls .play,.waiting .controls .play{display:none}
.finished .controls .again{display:inline-block}.playing .controls .play .busy{display:inline}
.dots{display:flex;gap:6px;flex:1;min-width:0}
.dot{flex:1;height:6px;border:0;border-radius:3px;background:var(--line);padding:0;position:relative;overflow:hidden;max-width:90px}
.dot.done{background:var(--steel)}
.dot.now{background:color-mix(in srgb,var(--steel) 45%,var(--line))}
.playing .dot.now::after{content:"";position:absolute;inset:0;background:var(--steel);transform-origin:left;animation:fill 6s linear forwards}
@keyframes fill{from{transform:scaleX(0)}to{transform:scaleX(1)}}
/* ---------------- how it works ---------------- */
.flow{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:0;counter-reset:s}
.flow li{counter-increment:s;padding:0 18px 0 0;position:relative}
.flow li::before{content:counter(s);display:grid;place-items:center;width:30px;height:30px;border-radius:50%;
border:1.5px solid var(--steel);color:var(--steel);font:600 14px var(--sans);margin-bottom:12px;background:var(--paper);position:relative;z-index:1}
.flow li:not(:last-child)::after{content:"";position:absolute;left:30px;right:0;top:15px;height:1.5px;background:var(--line)}
.flow li.human::before{background:var(--steel);color:var(--steel-ink)}
.flow b{display:block;font-weight:600;margin-bottom:4px}.flow p{margin:0;color:var(--ink2);font-size:14.5px}
.facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:32px;margin:44px 0 0}
.facts div{display:flex;flex-direction:column-reverse;justify-content:flex-end;gap:6px;border-top:2px solid var(--ink);padding-top:14px}
.facts dt{color:var(--ink2);font-size:14.5px;max-width:34ch}.facts dd{margin:0;font:600 2.2rem/1 var(--sans);letter-spacing:-.03em}
/* ---------------- engineers ---------------- */
.cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:40px;align-items:start}
.term{background:var(--term);color:var(--term-ink);border-radius:10px;padding:16px 18px;font:400 12.5px/1.6 var(--mono);
white-space:pre-wrap;word-break:break-word;max-height:460px;overflow:auto;margin:0}
.termhead{margin:0 0 8px;font:500 13px var(--mono);color:var(--muted)}
.term[data-title]::before{content:attr(data-title);display:block;color:var(--term-dim);margin-bottom:10px}
.term .c{color:var(--term-dim)}
h3.sub{font-size:1.1rem;margin:40px 0 12px;font-weight:600;letter-spacing:-.01em}
.diagram{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px}
.diagram svg{display:block;min-width:820px;width:100%;height:auto}
.zone{fill:none;stroke:var(--line);stroke-width:1.5;stroke-dasharray:5 4}
.box{fill:var(--paper);stroke:var(--line)}.box.key{stroke:var(--steel);stroke-width:1.5}
.zl{font:600 12.5px var(--sans);fill:var(--muted)}.bl{font:500 13.5px var(--sans);fill:var(--ink)}
.bs{font:400 11.5px var(--mono);fill:var(--muted)}.al{font:400 11.5px var(--sans);fill:var(--muted)}
.arrow{stroke:var(--muted);stroke-width:1.4;fill:none;marker-end:url(#tip)}.arrow.act{stroke:var(--steel)}
.tip{fill:var(--muted)}.tip.act{fill:var(--steel)}
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
.assure{list-style:none;padding:0;margin:0;display:grid;gap:14px}
.assure li{padding-left:18px;position:relative;color:var(--ink2);font-size:15px}
.assure li::before{content:"";position:absolute;left:0;top:10px;width:7px;height:7px;border-radius:2px;background:var(--steel)}
.assure b{color:var(--ink);font-weight:600}
footer{border-top:1px solid var(--line);padding:28px 0 48px;color:var(--muted);font-size:14px}
footer .wrap{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
@media (max-width:980px){.stage{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.stage .mind{grid-column:1/-1;order:3}
.tabs{grid-template-columns:repeat(4,minmax(190px,1fr));overflow-x:auto}.flow{grid-template-columns:minmax(0,1fr);gap:18px}
.flow li{display:grid;grid-template-columns:30px 1fr;gap:0 14px;padding:0}.flow li::before{margin:0;grid-row:span 2}.flow li::after{display:none}}
@media (max-width:880px){.hero{grid-template-columns:minmax(0,1fr);gap:20px;padding-top:40px}.cols,.facts{grid-template-columns:minmax(0,1fr)}
.detail{grid-template-columns:1fr}.runs .head{display:none}
.runs summary{display:flex;flex-wrap:wrap;gap:6px 10px;padding:14px 16px}.runs summary b{flex-basis:100%}.runs summary .num{display:none}.detail{padding:4px 16px 20px}
nav a{margin-left:14px}}
@media (max-width:640px){.stage{grid-template-columns:minmax(0,1fr)}.stage .mind{order:0}.pane{min-height:0}.scenario{padding:20px 16px 16px}
.shop{grid-template-columns:repeat(2,minmax(0,1fr))}.shop li{grid-template-columns:10px 1fr;gap:2px 8px}.shop .state{grid-column:2}
.score{grid-template-columns:minmax(0,1fr)}.controls{flex-wrap:wrap}.dots{order:-1;flex-basis:100%}.controls .play{margin-right:auto}}
@media (max-width:560px){nav{display:none}.wrap{padding-left:16px;padding-right:16px}}
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
    if ok:
        return f'<span class="ok">{t("correcto", "correct")}</span>'
    return f'<span class="bad">{t("incorrecto", "wrong")}</span>'


def _first_proposal(case: Case) -> str:
    proposals = case.live.get("proposals") or []
    return str(proposals[0]) if proposals else "none"


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
            f"<dt>{t('Hoy', 'Today')}</dt><dd>"
            + t(
                f"Con las reglas de hoy la misma respuesta da <code>{_e(current)}</code> ({'correcto' if current in case.actions else 'sigue mal'}): una regla añadida después de esta prueba.",
                f"With today's guards the same answer gives <code>{_e(current)}</code> ({'correct' if current in case.actions else 'still wrong'}): a rule added after this run.",
            )
            + "</dd>"
        )
    return f"""<details><summary><b>{_e(case.name.removeprefix("chaos-"))}</b>
<span class="num">{_seconds(lv.get("mttd_seconds"))}</span>
<span class="num">{_seconds(lv.get("detected_to_diagnosis_seconds"))}</span>
<span><code>{_e(lv.get("root_cause"))}</code></span>
<span><code>{_e(proposed)}</code></span>
<span>{_verdict(root_ok and action_ok)}</span></summary>
<div class="detail"><div><p class="termhead">{t("Exactamente lo que vio la IA", "Exactly what the AI saw")}</p><pre class="term">{_e(ev.render())}</pre></div>
<div class="answer"><h3>{t("Qué pasó", "What happened")}</h3><dl>
<dt>{t("Falla", "Fault")}</dt><dd>{bilingual(case.fault, fault_es(case.fault))}</dd>
<dt>{t("Detectada tras", "Detected after")}</dt><dd>{_seconds(lv.get("mttd_seconds"))}; {t("diagnóstico", "diagnosis")} {_seconds(lv.get("detected_to_diagnosis_seconds"))} {t("después", "later")}</dd>
<dt>{t("Causa", "Root cause")}</dt><dd><code>{_e(lv.get("root_cause"))}</code> {_verdict(root_ok)}</dd>
<dt>{t("Categoría", "Category")}</dt><dd><code>{_e(lv.get("category"))}</code></dd>
<dt>{t("Recomienda", "Recommends")}</dt><dd><code>{_e(lv.get("action"))}</code></dd>
<dt>{t("A la persona", "To the human")}</dt><dd><code>{_e(proposed)}</code> {_verdict(action_ok)}; {t("lo correcto", "right answer")}
<code>{_e(" or ".join(case.actions))}</code></dd>{now}
<dt>{t("Reglas fijas", "Rule-based")}</dt><dd><code>{_e(lv.get("baseline"))}</code></dd>
<dt>{t("Modelo", "Model")}</dt><dd>{_e(lv.get("model"))}, {_seconds(lv.get("model_seconds"))}</dd></dl>
<blockquote>{bilingual(str(lv.get("summary", "")), summary_es(str(lv.get("summary", ""))))}</blockquote></div></div></details>"""


def _eval_table(results: dict[str, Any]) -> str:
    labels = [c["label"] for c in results["configs"]]
    s = results["summary"]
    n = int(s[labels[0]]["cases"])

    def bar(value: int, cls: str = "") -> str:
        return f'<span class="bar"><s class="{cls}" style="width:{round(90 * value / n)}px"></s>{value}/{n}</span>'

    rows = [
        f"<tr><td>{t('reglas fijas, sin IA', 'rule-based baseline')}</td>"
        f"<td>{bar(int(s[labels[0]]['baseline_root_cause']), 'base')}</td><td>{t('no medido', 'not measured')}</td><td>-</td><td>{t('instantáneo', 'instant')}</td></tr>"
    ]
    for label in labels:
        m = s[label]
        chosen = label == DEFAULT_MODEL
        tag = f'<span class="tag">{t("en uso", "in use")}</span>' if chosen else ""
        rows.append(
            f'<tr class="{"chosen" if chosen else ""}"><td>{_e(label)}{tag}</td>'
            f"<td>{bar(int(m['root_cause']))}</td><td>{m['right_action']}/{n}</td>"
            f"<td>{m['harmful']}</td><td>{m['median_seconds']} s</td></tr>"
        )
    return (
        f'<div class="tablebox"><table><thead><tr><th>{t("Configuración", "Configuration")}</th><th>{t("Causa correcta", "Root cause right")}</th>'
        f"<th>{t('Acción correcta llega a la persona', 'Right action reaches the human')}</th><th>{t('Acciones equivocadas', 'Wrong actions proposed')}</th>"
        f"<th>{t('Tiempo mediano', 'Median time')}</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _redteam_table(files: list[Path]) -> str:
    body = ""
    for path in files:
        data = json.loads(path.read_text())
        scenario = data.get("scenario") or path.stem.split("-", 3)[-1]
        tot = data["totals"]
        cells = "".join(
            f"<td>{tot[k]['hijacked']} / {tot[k]['attacks']}</td>" for k in ("L0", "L1", "L2")
        )
        body += f"<tr><td><code>{_e(scenario)}</code></td>{cells}</tr>"
    return (
        f'<div class="tablebox"><table><thead><tr><th>{t("Incidente", "Incident")}</th><th>{t("Sin defensas", "No defences")}</th>'
        f"<th>{t('Solo defensas en las instrucciones', 'Prompt defences only')}</th><th>{t('Reglas en código (Centinela)', 'Guards in code (Centinela)')}</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


RUN_IT = """<span class="c"><span class="es"># necesitas Docker, uv, k3d, kubectl, Terraform y Ollama (Linux, macOS o WSL2)</span><span class="en"># needs Docker, uv, k3d, kubectl, Terraform and Ollama (Linux, macOS or WSL2)</span></span>
git clone https://github.com/Fabricioanchundia/IA-CENTINELA
cd IA-CENTINELA && uv sync --all-packages
ollama pull qwen3:8b            <span class="c"><span class="es"># el modelo, unos 5 GB</span><span class="en"># the model, about 5 GB</span></span>
<span class="c"><span class="es"># clúster nuevo, se rompe pagos, apruebas y ves la reparación (unos 20 minutos)</span><span class="en"># fresh cluster, break payments, approve, watch the repair (about 20 minutes)</span></span>
./scripts/e2e.sh
<span class="c"><span class="es"># ¿sin GPU? usa un modelo pequeño en la CPU</span><span class="en"># no GPU? use a small model on the CPU</span></span>
ollama pull qwen3:1.7b && E2E_MODEL=qwen3:1.7b ./scripts/e2e.sh
<span class="c"><span class="es"># repite los incidentes grabados con tu propio modelo</span><span class="en"># replay the recorded incidents on your own model</span></span>
uv run centinela eval run"""


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
    attacks = sum(json.loads(p.read_text())["totals"]["L2"]["attacks"] for p in redteam)
    got_through = sum(json.loads(p.read_text())["totals"]["L2"]["hijacked"] for p in redteam)
    player = render(scenarios(cases, root / "docs" / "redteam", {c.name: today(c) for c in cases}))
    rows = "".join(_run_row(c) for c in cases)
    n = default["cases"]
    before = slow["L1"]["hijacked"] if slow else "-"
    return f"""<!doctype html><html lang="en" data-lang="en" data-title-es="Centinela: IA que diagnostica caídas, con una persona al mando"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="referrer" content="no-referrer">
<title>Centinela: AI that diagnoses outages, with a person in charge</title>
<meta name="description" content="Centinela finds the cause of an outage in a Kubernetes system and proposes the fix. A person approves it. Watch it handle real recorded incidents.">
<style>{CSS}</style>
<script src="player.js" defer></script></head><body>
<header class="top"><div class="wrap"><a class="mark" href="#">Centinela<span>v{_e(__version__)}</span></a>
<div class="right"><nav><a href="#demo">{t("Demostración", "Demo")}</a><a href="#how">{t("Cómo funciona", "How it works")}</a><a href="#engineers">{t("Para ingenieros", "For engineers")}</a><a href="{REPO}">GitHub</a></nav>
<div class="lang" role="group" aria-label="Language"><button type="button" data-set-lang="es" aria-pressed="false">ES</button><button type="button" data-set-lang="en" aria-pressed="false">EN</button></div></div></div></header>
<main>
<div class="wrap hero">
<h1>{t("Cuando tu sistema falla, Centinela encuentra la causa y propone el arreglo. Una persona decide.", "When your system fails, Centinela finds the cause and proposes the fix. A person decides.")}</h1>
<div><p class="lede">{t("Es un agente de IA para equipos de operaciones. Abajo puedes ver cómo manejó fallas reales que provocamos a propósito en una tienda en línea: qué vio, qué concluyó y quién tuvo la última palabra.", "An AI agent for operations teams. Below, watch how it handled real faults we caused on purpose in an online shop: what it saw, what it concluded and who had the last word.")}</p>
<p class="links"><a href="#demo">{t("Probar el simulador", "Try the simulator")}</a><a href="#try">{t("Probar la IA real en tu máquina", "Run the real AI yourself")}</a><a href="{REPO}">{t("Código fuente", "Source code")}</a></p></div>
</div>
<section class="demo"><div class="wrap">{player}</div></section>

<section id="how"><div class="wrap">
<h2>{t("Cómo funciona", "How it works")}</h2>
<p class="intro">{t("La IA investiga y propone. Nunca cambia nada por su cuenta: entre la propuesta y el cambio siempre hay una persona.", "The AI investigates and proposes. It never changes anything on its own: there is always a person between the proposal and the change.")}</p>
<ol class="flow">
<li><b>{t("Vigila", "Watches")}</b><p>{t("Las alarmas de la tienda abren un incidente y agrupan las señales relacionadas.", "The shop's alerts open an incident and group the related signals.")}</p></li>
<li><b>{t("Investiga", "Investigates")}</b><p>{t("Reúne métricas, registros, el estado de Kubernetes, manuales e incidentes pasados.", "Gathers metrics, logs, Kubernetes state, runbooks and past incidents.")}</p></li>
<li><b>{t("Propone", "Proposes")}</b><p>{t("Un modelo local explica la causa. Centinela revisa la respuesta y propone una sola acción segura.", "A local model explains the cause. Centinela checks the answer and proposes one safe action.")}</p></li>
<li class="human"><b>{t("Una persona aprueba", "A person approves")}</b><p>{t("Desde la consola o la terminal. Si nadie responde en 15 minutos, la propuesta vence.", "From the console or the terminal. If nobody answers in 15 minutes, the proposal expires.")}</p></li>
<li><b>{t("Ejecuta y verifica", "Applies and verifies")}</b><p>{t("Un componente con permisos mínimos aplica el cambio y comprueba que funcionó.", "A component with minimal permissions applies the change and checks it worked.")}</p></li>
</ol>
<dl class="facts">
<div><dt>{t("fallas reales provocadas en el sistema, con la causa encontrada correctamente", "real faults caused in the system, with the cause found correctly")}</dt><dd>{live_ok} {t("de", "of")} {len(cases)}</dd></div>
<div><dt>{t(f"acciones equivocadas propuestas en {n} incidentes de prueba", f"wrong actions proposed in {n} test incidents")}</dt><dd>{default["harmful"]}</dd></div>
<div><dt>{t(f"de {attacks} ataques para engañar a la IA lograron su objetivo (con defensas solo en las instrucciones: {before})", f"of {attacks} attacks to trick the AI got their way (with defences only in the instructions: {before})")}</dt><dd>{got_through}</dd></div>
</dl></div></section>

<section id="engineers"><div class="wrap">
<h2>{t("Para ingenieros", "For engineers")}</h2>
<p class="intro">{t("Python 3.12, Kubernetes (k3s), Terraform, Prometheus, Loki, PostgreSQL, Ollama y un servidor MCP. Cada pull request levanta un clúster nuevo y repite la reparación de punta a punta.", "Python 3.12, Kubernetes (k3s), Terraform, Prometheus, Loki, PostgreSQL, Ollama and an MCP server. Every pull request builds a fresh cluster and repeats the repair end to end.")}</p>
<div class="cols"><div>
<h3 class="sub" id="try" style="margin-top:0">{t("Prueba la IA real en tu máquina", "Try the real AI on your machine")}</h3>
<pre class="term">{RUN_IT}</pre>
</div><div>
<h3 class="sub" style="margin-top:0">{t("Dónde comprobarlo", "Where to check it")}</h3>
<ul class="assure">
<li><b><a href="{REPO}/actions/workflows/e2e.yml">{t("Prueba de punta a punta", "End-to-end test")}</a>.</b> {t("Cada ejecución: clúster nuevo, payments en 0, diagnóstico, aprobación, reparación.", "Every run: fresh cluster, payments at 0, diagnosis, approval, repair.")}</li>
<li><b><a href="{REPO}/tree/main/docs/adr">{t("Decisiones de diseño", "Design decisions")}</a>.</b> {t("Doce ADR: por qué cada pieza es como es.", "Twelve ADRs: why each piece is the way it is.")}</li>
<li><b><a href="{REPO}/tree/main/docs/eval">{t("Evaluación", "Evaluation")}</a> {t("y", "and")} <a href="{REPO}/tree/main/docs/redteam">red team</a>.</b> {t("Resultados completos y cómo repetirlos.", "Full results and how to repeat them.")}</li>
<li><b>{t("Imágenes firmadas", "Signed images")}.</b> {t("Con cosign, SBOM y procedencia, escaneadas con Trivy.", "With cosign, SBOM and provenance, scanned with Trivy.")}</li>
</ul></div></div>

<h3 class="sub">{t("Arquitectura", "Architecture")}</h3>
<p class="intro">{t("El modelo nunca tiene credenciales ni acceso a la API del clúster; el componente que puede cambiar el clúster no piensa.", "The model never holds credentials or reaches the cluster API; the component that can change the cluster cannot think.")}</p>
<div class="diagram">{bilingual_svg(ARCHITECTURE)}</div>

<h3 class="sub">{t(f"Evaluación con {n} incidentes reproducibles", f"Evaluation on {n} replayable incidents")}</h3>
<p class="intro">{t("Incidentes grabados y sintéticos, pasados por el mismo prompt, validación y reglas que en producción, con <code>centinela eval run</code>.", "Recorded and synthetic incidents through the production prompt, validation and guards, with <code>centinela eval run</code>.")}</p>
{_eval_table(results)}

<h3 class="sub">{t("Inyección de instrucciones en los registros", "Prompt injection in logs")}</h3>
<p class="intro">{t("Ataques escondidos en registros, eventos de Kubernetes e incidentes pasados, medidos con <code>centinela redteam</code>: cuántos pusieron la acción del atacante frente al ingeniero.", "Attacks hidden in logs, Kubernetes events and past incidents, measured with <code>centinela redteam</code>: how many put the attacker's action in front of the engineer.")}</p>
{_redteam_table(redteam)}

<h3 class="sub" id="replay">{t("Todos los incidentes de la prueba de caos", "Every incident from the chaos run")}</h3>
<p class="intro">{t("Diez fallas provocadas con <code>scripts/chaos.sh</code>; ocho debían abrir un incidente y lo abrieron. Abre una fila para ver la evidencia exacta.", "Ten faults injected with <code>scripts/chaos.sh</code>; eight should open an incident and did. Open a row for the exact evidence.")}</p>
<div class="runs"><div class="head"><span>{t("Falla", "Fault")}</span><span>{t("Detectada tras", "Detected after")}</span><span>{t("Diagnóstico tras", "Diagnosis after")}</span>
<span>{t("Causa", "Root cause")}</span><span>{t("Propuesta a la persona", "Proposed to the engineer")}</span><span>{t("Resultado", "Result")}</span></div>{rows}</div>
<p class="note">{t("Tiempos medidos durante la prueba. La detección de catalog-down se infló por una alarma tardía de la falla anterior; desde v0.9 una alarma tardía reabre su propio incidente.", "Times as measured during the run. The catalog-down detection time was inflated by a late alert left over from the previous fault; since v0.9 a late alert reopens its own incident.")}</p>

<h3 class="sub">{t("Por qué esta página es segura", "Why this page is safe")}</h3>
<ul class="assure">
<li><b>{t("Sin servidor.", "No server.")}</b> {t("No hay nada a qué iniciar sesión ni nada que ejecutar; la consola real de aprobación solo escucha dentro del clúster.", "Nothing to log into, nothing to run; the real approval console only listens inside the cluster.")}</li>
<li><b>{t("Un solo script, sin red.", "One script, no network.")}</b> {t("<code>player.js</code> cambia pasos e idioma. La política de seguridad (CSP) no permite conexiones, scripts de terceros ni scripts en línea.", "<code>player.js</code> switches steps and language. The Content-Security-Policy allows no connections, third-party scripts or inline scripts.")}</li>
<li><b>{t("Todo escapado.", "Everything escaped.")}</b> {t("Cada registro y respuesta del modelo se muestra como texto.", "Every log line and model answer is shown as text.")}</li>
<li><b>{t("Construida en abierto.", "Built in the open.")}</b> {t("GitHub Actions la genera desde los archivos del repositorio.", "GitHub Actions generates it from files in the repository.")}</li>
</ul>
</div></section>
</main>
<footer><div class="wrap"><span>Centinela v{_e(__version__)}. {t("Proyecto de Fabricio Anchundia.", "A project by Fabricio Anchundia.")}</span>
<a href="{REPO}">{REPO.removeprefix("https://")}</a></div></footer>
</body></html>
"""


def write(root: Path, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(build(root))
    for name in STATIC:
        shutil.copyfile(ASSETS / name, out / name)
    (out / ".nojekyll").write_text("")
    return page
