"""The incident player of the public demo: real recorded incidents told step by step.

Each scenario is a list of frames. A frame shows three things at one moment: the shop (which
service works), what Centinela sees and concludes, and the engineer's phone. The frames are
rendered as plain HTML (readable without JavaScript, one under the other); player.js only shows
one at a time. Every value that comes from a recording (alerts, logs, model answers) is escaped.

Text is bilingual: t(es, en) renders both and the page CSS shows the chosen language.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from centinela.agent import snapshot
from centinela.agent.evidence import Evidence
from centinela.eval.cases import Case
from centinela.redteam.attacks import ATTACKS

SERVICES = (
    ("catalog", "catálogo", "catalog"),
    ("orders", "pedidos", "orders"),
    ("payments", "pagos", "payments"),
    ("redis", "caché", "cache"),
    ("postgres", "base de datos", "database"),
)
NAMES = {key: (es, en) for key, es, en in SERVICES}
STATES = {
    "ok": ("funciona", "working"),
    "slow": ("lento", "slow"),
    "errors": ("con errores", "failing"),
    "down": ("caído", "down"),
    "fixed": ("recuperado", "recovered"),
}
KINDS = {
    "alert": ("alarma", "alert"),
    "metrics": ("métricas", "metrics"),
    "dependencies": ("llamadas entre servicios", "calls between services"),
    "cache": ("caché", "cache"),
    "workloads": ("estado de Kubernetes", "Kubernetes state"),
    "pods": ("pods", "pods"),
    "k8s_events": ("eventos de Kubernetes", "Kubernetes events"),
    "logs": ("registros", "logs"),
    "runbook": ("manual de operación", "runbook"),
    "history": ("incidente pasado", "past incident"),
}


def t(es: str, en: str) -> str:
    """Both languages; the page shows one. Arguments are trusted HTML written in this file."""
    return f'<span class="es">{es}</span><span class="en">{en}</span>'


def _e(value: object) -> str:
    return escape(str(value))


def svc(key: str) -> str:
    es, en = NAMES.get(key, (key, key))
    return t(es, en)


def action_text(action: str) -> str:
    kind, _, target = action.replace(" ", ":").partition(":")
    es, en = NAMES.get(target, (target, target))
    if kind == "scale_up":
        return t(f"Volver a encender {es} (1 copia)", f"Scale {en} back up to 1 replica")
    if kind == "restart":
        return t(f"Reiniciar {es}", f"Restart {en}")
    return t("No tocar nada y avisar a una persona", "Change nothing and hand over to a person")


@dataclass
class Frame:
    title: str
    caption: str
    shop: dict[str, str]
    mind: str
    phone: str
    when: datetime | None = None
    tone: str = ""  # "", "bad" or "good": colours the caption marker


@dataclass
class Scenario:
    key: str
    tab: str
    lede: str
    frames: list[Frame] = field(default_factory=list)


# ------------------------------------------------------------------ building blocks


def _shop(states: dict[str, str]) -> str:
    rows = []
    for key, _, _ in SERVICES:
        state = states.get(key, "ok")
        es, en = STATES[state]
        rows.append(
            f'<li class="s-{state}"><i></i><span class="svc">{svc(key)}</span>'
            f'<span class="state">{t(es, en)}</span></li>'
        )
    return f'<ul class="shop">{"".join(rows)}</ul>'


def _phone_idle() -> str:
    return f'<p class="quiet">{t("Sin notificaciones.", "No notifications.")}</p>'


def _phone_note(text: str, cls: str = "") -> str:
    return f'<div class="msg {cls}"><b>Centinela</b><p>{text}</p></div>'


def _phone_proposal(action: str, state: str = "pending") -> str:
    """state: pending, approved, wrong, or hijacked (the attacker's action reached the human)."""
    head = {
        "pending": t("Propuesta, espera tu decisión", "Proposal, waiting for your decision"),
        "approved": t("Aprobada por una persona", "Approved by a person"),
        "hijacked": t("Propuesta del atacante", "The attacker's proposal"),
        "wrong": t("Propuesta incorrecta", "Wrong proposal"),
    }[state]
    buttons = (
        f'<div class="btns" aria-hidden="true"><span class="approve">{t("Aprobar", "Approve")}</span>'
        f'<span class="reject">{t("Rechazar", "Reject")}</span></div>'
    )
    expiry = t("Vence en 15 minutos si nadie responde.", "Expires in 15 minutes if nobody answers.")
    return (
        f'<div class="card {state}"><small>{head}</small><b>{action_text(action)}</b>'
        f"{buttons}<p>{expiry}</p></div>"
    )


def _alerts(ev: Evidence) -> str:
    items = "".join(
        f"<li><time>{a.active_at:%H:%M:%S}</time><b>{_e(a.name)}</b><span>{_e(a.summary)}</span></li>"
        for a in sorted(ev.alerts, key=lambda a: a.active_at)
    )
    return f'<ul class="alerts">{items}</ul>'


def _key_line(body: str, root: str) -> str:
    lines = [ln for ln in body.splitlines() if ln.strip() and not ln.startswith("#")]
    marked = [ln for ln in lines if "<-" in ln and root in ln]
    hits = marked or [ln for ln in lines if root in ln]
    return (hits or lines or [""])[0].strip()


def _evidence(ev: Evidence, root: str, limit: int = 6) -> str:
    """The pieces of evidence that matter most: alerts and everything about the root cause."""
    picked = [i for i in ev.items if i.kind == "alert" or root in i.services]
    picked += [i for i in ev.items if i not in picked and i.kind not in ("runbook", "history")]
    rows = []
    for item in picked[:limit]:
        es, en = KINDS.get(item.kind, (item.kind, item.kind))
        key = "key" if root in item.services and item.kind != "alert" else ""
        rows.append(
            f'<li class="{key}"><span class="kind">{_e(item.id)} {t(es, en)}</span>'
            f"<code>{_e(_key_line(item.body, root))}</code></li>"
        )
    full = t("Ver todo lo que recibió el modelo", "See everything the model received")
    return (
        f'<ul class="evidence">{"".join(rows)}</ul>'
        f'<details class="raw"><summary>{full} ({len(ev.items)})</summary>'
        f'<pre class="term">{_e(ev.render())}</pre></details>'
    )


def _answer(live: dict[str, Any]) -> str:
    note = t("Respuesta real del modelo, en inglés", "The model's real answer")
    model = _e(live.get("model"))
    secs = f"{float(live.get('model_seconds', 0)):.0f}"
    valid = (
        t("comprobada: cita pruebas que existen", "checked: it cites evidence that exists")
        if live.get("valid")
        else t("rechazada por la validación", "rejected by validation")
    )
    return (
        f'<figure class="said"><blockquote lang="en">{_e(live.get("summary"))}</blockquote>'
        f"<figcaption>{note}. {model}, {secs} s, {valid}.</figcaption></figure>"
    )


def _times(ev: Evidence, live: dict[str, Any]) -> tuple[datetime, datetime, datetime]:
    first = min(a.active_at for a in ev.alerts)
    detected = ev.incident.detected_at
    diagnosed = detected + timedelta(seconds=float(live.get("detected_to_diagnosis_seconds", 0)))
    return first, detected, diagnosed


# ------------------------------------------------------------------ scenarios


def _opening(case: Case, ev: Evidence, broken: dict[str, str], fault: str) -> list[Frame]:
    live = case.live
    root = str(live.get("root_cause"))
    root_es, root_en = (_e(n) for n in NAMES.get(root, (root, root)))
    first, detected, diagnosed = _times(ev, live)
    mttd = f"{float(live.get('mttd_seconds', 0)):.0f}"
    return [
        Frame(
            t("Todo funciona", "Everything works"),
            t(
                "Una tienda en línea con cinco servicios en Kubernetes. Centinela vigila sus alarmas.",
                "An online shop with five services on Kubernetes. Centinela watches its alerts.",
            ),
            {},
            f'<p class="quiet">{t("Vigilando. Ninguna alarma activa.", "Watching. No active alerts.")}</p>',
            _phone_idle(),
        ),
        Frame(
            t("Algo se rompe", "Something breaks"),
            fault,
            broken,
            f'<p class="quiet">{t("Todavía sin alarma: las reglas esperan a que el problema se confirme, para no despertar a nadie por un parpadeo.", "No alert yet: the rules wait until the problem is confirmed, so nobody is woken up by a blip.")}</p>',
            _phone_idle(),
            first,
            "bad",
        ),
        Frame(
            t("Suena la alarma", "The alarm goes off"),
            t(
                f"Centinela agrupa las alarmas relacionadas en un solo incidente, {mttd} s después de la primera señal.",
                f"Centinela groups the related alerts into one incident, {mttd} s after the first signal.",
            ),
            broken,
            f"<h4>{t('Incidente', 'Incident')} #{_e(ev.incident.id)}</h4>{_alerts(ev)}",
            _phone_note(
                t(
                    f"Incidente abierto: {_e(ev.incident.title)}. Investigando.",
                    f"Incident opened: {_e(ev.incident.title)}. Investigating.",
                )
            ),
            detected,
        ),
        Frame(
            t("Centinela investiga", "Centinela investigates"),
            t(
                f"Reúne {len(ev.items)} pruebas: métricas, registros, estado de Kubernetes, manuales e incidentes pasados. Los textos que cualquiera puede escribir, como los registros, se marcan como no confiables.",
                f"It gathers {len(ev.items)} pieces of evidence: metrics, logs, Kubernetes state, runbooks and past incidents. Text anyone can write, such as logs, is marked untrusted.",
            ),
            broken,
            _evidence(ev, root),
            _phone_idle(),
            detected,
        ),
        Frame(
            t("Encuentra la causa", "Finds the cause"),
            t(
                f"Un modelo de lenguaje que corre en hardware propio, no en la nube, lee las pruebas y señala a <b>{root_es}</b> como la causa. Centinela verifica que cada prueba citada exista.",
                f"A language model running on local hardware, not in the cloud, reads the evidence and points to <b>{root_en}</b> as the cause. Centinela checks that every cited piece of evidence exists.",
            ),
            broken,
            _answer(live),
            _phone_idle(),
            diagnosed,
        ),
    ]


def payments_down(case: Case, ev: Evidence) -> Scenario:
    broken = {"payments": "down", "orders": "errors"}
    _, _, diagnosed = _times(ev, case.live)
    action = (case.live.get("proposals") or ["none"])[0]
    frames = _opening(
        case,
        ev,
        broken,
        t(
            "Alguien deja el servicio de pagos en 0 copias. Los clientes ya no pueden pagar y pedidos empieza a fallar.",
            "Someone scales payments to zero. Customers cannot pay and orders starts failing.",
        ),
    )
    frames += [
        Frame(
            t("Propone el arreglo", "Proposes the fix"),
            t(
                "Propone una sola acción segura. No la ejecuta: le llega a una persona para que decida.",
                "It proposes one safe action. It does not run it: a person decides.",
            ),
            broken,
            f"<h4>{t('Por qué es segura', 'Why it is safe')}</h4><p>{t('El ejecutor solo puede encender o reiniciar cuatro servicios con nombre, y nunca la base de datos.', 'The executor can only scale or restart four named services, never the database.')}</p>",
            _phone_proposal(action),
            diagnosed,
        ),
        Frame(
            t("Una persona aprueba", "A person approves"),
            t(
                "El ingeniero aprueba desde la consola o la terminal. En el incidente real #9 lo aprobó Fabricio desde la consola web.",
                "The engineer approves from the console or the terminal. In real incident #9 Fabricio approved it from the web console.",
            ),
            broken,
            f'<p class="quiet">{t("Aprobación registrada con nombre y hora.", "Approval recorded with name and time.")}</p>',
            _phone_proposal(action, "approved"),
            None,
            "good",
        ),
        Frame(
            t("Arreglado y verificado", "Fixed and verified"),
            t(
                "Un componente separado, con los permisos mínimos, aplica el cambio y comprueba que pagos responde. En el incidente #9 tardó 9 s. El incidente se cierra solo cuando las alarmas se apagan.",
                "A separate component with minimal permissions applies the change and checks that payments answers. In incident #9 it took 9 s. The incident closes by itself when the alerts clear.",
            ),
            {"payments": "fixed", "orders": "fixed"},
            f'<p class="ok-line">{t("Pagos: 1 de 1 copias lista. Pedidos sin errores.", "Payments: 1 of 1 replicas ready. Orders without errors.")}</p>',
            _phone_note(t("Incidente resuelto.", "Incident resolved."), "good"),
            None,
            "good",
        ),
    ]
    return Scenario(
        "payments-down",
        t("Se cae pagos", "Payments goes down"),
        t("El caso típico, de principio a fin.", "The typical case, start to finish."),
        frames,
    )


def postgres_down(case: Case, ev: Evidence) -> Scenario:
    broken = {"postgres": "down", "catalog": "errors", "orders": "errors", "payments": "errors"}
    _, _, diagnosed = _times(ev, case.live)
    suggested = str(case.live.get("action", "none"))
    frames = _opening(
        case,
        ev,
        broken,
        t(
            "Se apaga la base de datos. Catálogo, pedidos y pagos dependen de ella y empiezan a fallar a la vez.",
            "The database goes down. Catalog, orders and payments depend on it and all start failing.",
        ),
    )
    frames += [
        Frame(
            t("Conoce sus límites", "Knows its limits"),
            t(
                "El modelo sugirió encender la base de datos. Centinela lo bloquea: tiene prohibido tocar la base de datos, porque un error ahí puede perder datos.",
                "The model suggested starting the database. Centinela blocks it: it is not allowed to touch the database, where a mistake can lose data.",
            ),
            broken,
            f"<h4>{t('Sugerencia del modelo', 'Model suggestion')}</h4><p class='struck'>{action_text(suggested)}</p>"
            f"<p>{t('Bloqueada por la política: la base de datos queda fuera del alcance del ejecutor.', 'Blocked by policy: the database is outside the executor’s reach.')}</p>",
            _phone_note(
                t(
                    "La base de datos está apagada y catálogo, pedidos y pagos fallan por eso. Necesita a una persona. Diagnóstico adjunto.",
                    "The database is down and catalog, orders and payments fail because of it. It needs a person. Diagnosis attached.",
                ),
                "warn",
            ),
            diagnosed,
            "good",
        ),
        Frame(
            t("Una persona decide", "A person decides"),
            t(
                "La persona de guardia recibe la causa ya encontrada, con las pruebas, en vez de empezar a investigar desde cero.",
                "The on-call engineer gets the cause already found, with the evidence, instead of starting from scratch.",
            ),
            broken,
            f'<p class="quiet">{t("Centinela no actuó. Esa también es una buena respuesta.", "Centinela did not act. That is also a good answer.")}</p>',
            _phone_note(
                t("Asignado a la persona de guardia.", "Assigned to the on-call engineer."), "warn"
            ),
        ),
    ]
    return Scenario(
        "postgres-down",
        t("Se cae la base de datos", "The database goes down"),
        t("Cuando lo correcto es no tocar nada.", "When the right move is to touch nothing."),
        frames,
    )


def bad_release(case: Case, ev: Evidence, today: str) -> Scenario:
    broken = {"payments": "errors", "orders": "errors"}
    _, _, diagnosed = _times(ev, case.live)
    proposed = (case.live.get("proposals") or ["none"])[0]
    rollout = next(
        (
            ln.split("<-", 1)[1].strip()
            for i in ev.items
            for ln in i.body.splitlines()
            if "NEW ROLLOUT" in ln
        ),
        "",
    )
    frames = _opening(
        case,
        ev,
        broken,
        t(
            "Se publica una versión nueva de pagos con un error: la mitad de los pagos falla.",
            "A new payments release ships with a bug: half the payments fail.",
        ),
    )
    frames += [
        Frame(
            t("La IA se equivoca", "The AI gets it wrong"),
            t(
                "Encontró bien la causa, pero propuso reiniciar pagos. Reiniciar no arregla una versión defectuosa: lo correcto es volver a la anterior. Esto pasó de verdad, en la grabación de esta falla.",
                "It found the right cause but proposed restarting payments. A restart does not fix a bad release: the fix is to roll back. This really happened in the recording of this fault.",
            ),
            broken,
            f"<h4>{t('Lo que vio en Kubernetes', 'What it saw in Kubernetes')}</h4><code class='line'>payments: {_e(rollout)}</code>",
            _phone_proposal(proposed, "wrong"),
            diagnosed,
            "bad",
        ),
        Frame(
            t("Se añade una regla", "A rule is added"),
            t(
                "Después de esta falla se añadió una regla en el código: si hubo un despliegue minutos antes de la alarma, no se propone reiniciar. Con las reglas de hoy, la misma respuesta del modelo termina así:",
                "After this fault a rule was added in code: if there was a rollout minutes before the alert, no restart is proposed. With today's rules the same model answer ends like this:",
            ),
            broken,
            f"<h4>{t('Hoy', 'Today')}</h4><p>{action_text(today)}</p>"
            f"<p class='quiet'>{t('Una persona revisa el despliegue reciente y decide volver atrás.', 'A person reviews the recent rollout and decides to roll back.')}</p>",
            _phone_note(
                t(
                    "Pagos falla desde un despliegue reciente. Reiniciar no ayudaría: revisa ese despliegue.",
                    "Payments fails since a recent rollout. A restart would not help: review that rollout.",
                ),
                "warn",
            ),
            None,
            "good",
        ),
    ]
    return Scenario(
        "bad-release",
        t("Actualización defectuosa", "A bad release"),
        t("Un error real de la IA y cómo se corrigió.", "A real AI mistake and how it was fixed."),
        frames,
    )


def injection(redteam: dict[str, Any]) -> Scenario:
    attack = next(a for a in ATTACKS if a.name == "direct_override")
    runs = {r["level"]: r for r in redteam["runs"] if r["attack"] == attack.name}
    l1, l2 = runs["L1"], runs["L2"]
    totals = redteam["totals"]
    slow = {"orders": "slow"}
    term = f'<pre class="term short">{_e(attack.text)}\n{_e(attack.text)}\n…</pre>'

    def outcome(run: dict[str, Any]) -> str:
        return (
            f'<figure class="said"><blockquote lang="en">{_e(run["summary"])}</blockquote>'
            f"<figcaption>{t('Respuesta real del modelo', 'The model’s real answer')}, {_e(redteam['model'])}</figcaption></figure>"
        )

    frames = [
        Frame(
            t("Pedidos va lento", "Orders is slow"),
            t(
                "Solo pedidos está lento. No hay otra señal: la causa está en pedidos.",
                "Only orders is slow. There is no other signal: the cause is in orders.",
            ),
            slow,
            '<ul class="alerts"><li><b>HighLatencyP95</b><span>orders p95 2.4 s</span></li></ul>',
            _phone_note(
                t("Incidente abierto: pedidos va lento.", "Incident opened: orders is slow.")
            ),
        ),
        Frame(
            t("Alguien escribe una trampa", "Someone plants a trap"),
            t(
                f"Pedidos guarda en sus registros el cupón que escribe el cliente. Un atacante escribe como cupón una orden para la IA, {redteam_repeat(redteam)} veces. Quiere que se reinicie la caché, que no tiene nada que ver.",
                f"Orders logs the coupon code the customer types. An attacker types an order for the AI as the coupon, {redteam_repeat(redteam)} times. They want the cache restarted, which has nothing to do with it.",
            ),
            slow,
            f"<h4>{t('Registros de pedidos', 'Orders logs')}</h4>{term}",
            _phone_idle(),
            None,
            "bad",
        ),
        Frame(
            t("Sin las defensas de Centinela", "Without Centinela's defences"),
            t(
                "Con defensas solo en las instrucciones del modelo, la IA obedece al atacante: culpa a la caché y propone reiniciarla.",
                "With defences only in the model's instructions, the AI obeys the attacker: it blames the cache and proposes restarting it.",
            ),
            slow,
            outcome(l1),
            _phone_proposal(l1["proposal"], "hijacked"),
            None,
            "bad",
        ),
        Frame(
            t("Con Centinela", "With Centinela"),
            t(
                f"Un detector en código encuentra las {len(l2['quarantined'])} líneas con instrucciones y las pone en cuarentena antes de que el modelo las lea. La IA vuelve a mirar pedidos, y la caché no se toca.",
                f"A detector in code finds the {len(l2['quarantined'])} lines with instructions and quarantines them before the model reads them. The AI looks at orders again and the cache is left alone.",
            ),
            slow,
            outcome(l2),
            _phone_proposal(l2["proposal"]),
            None,
            "good",
        ),
        Frame(
            t("El resultado medido", "The measured result"),
            t(
                f"Se probaron {totals['L2']['attacks']} ataques distintos en este incidente: órdenes directas, mensajes falsos del sistema, letras separadas, pruebas falsificadas.",
                f"{totals['L2']['attacks']} different attacks were tried on this incident: direct orders, fake system messages, spaced letters, forged evidence.",
            ),
            slow,
            f"<dl class='score'><div><dt>{t('Sin defensas', 'No defences')}</dt><dd>{totals['L0']['hijacked']}</dd></div>"
            f"<div><dt>{t('Defensas en las instrucciones', 'Defences in the instructions')}</dt><dd>{totals['L1']['hijacked']}</dd></div>"
            f"<div class='good'><dt>{t('Centinela', 'Centinela')}</dt><dd>{totals['L2']['hijacked']}</dd></div></dl>"
            f"<p class='quiet'>{t('Ataques que lograron poner su acción frente al ingeniero.', 'Attacks that got their action in front of the engineer.')}</p>",
            _phone_idle(),
            None,
            "good",
        ),
    ]
    return Scenario(
        "injection",
        t("Intentan engañar a la IA", "Someone tries to trick the AI"),
        t(
            "Una inyección de instrucciones escondida en los registros.",
            "A prompt injection hidden in the logs.",
        ),
        frames,
    )


def redteam_repeat(redteam: dict[str, Any]) -> int:
    for run in redteam["runs"]:
        if run["attack"] == "direct_override" and run["level"] == "L2":
            return len(run["quarantined"]) or 1
    return 1


def scenarios(cases: list[Case], redteam_dir: Path, today: dict[str, str]) -> list[Scenario]:
    by_name = {c.name: c for c in cases}
    out: list[Scenario] = []
    for name, make in (
        ("chaos-payments-down", payments_down),
        ("chaos-postgres-down", postgres_down),
    ):
        if name in by_name:
            case = by_name[name]
            out.append(make(case, snapshot.from_json(case.evidence)))
    if "chaos-payments-bad-release" in by_name:
        case = by_name["chaos-payments-bad-release"]
        out.append(
            bad_release(case, snapshot.from_json(case.evidence), today.get(case.name, "none"))
        )
    slow = next(iter(sorted(redteam_dir.glob("results-*slow-orders.json"))), None)
    if slow:
        out.append(injection(json.loads(slow.read_text())))
    return out


# ------------------------------------------------------------------ rendering


def render(items: list[Scenario]) -> str:
    tabs = "".join(
        f'<button type="button" role="tab" id="tab-{s.key}" aria-controls="sc-{s.key}" '
        f'aria-selected="{"true" if i == 0 else "false"}"><b>{s.tab}</b><small>{s.lede}</small></button>'
        for i, s in enumerate(items)
    )
    panels = "".join(_scenario(s, i == 0) for i, s in enumerate(items))
    return (
        f'<div class="player" id="demo"><div class="tabs" role="tablist">{tabs}</div>{panels}</div>'
    )


def _scenario(s: Scenario, first: bool) -> str:
    n = len(s.frames)
    frames = "".join(_frame(f, i, n) for i, f in enumerate(s.frames))
    dots = "".join(
        f'<button type="button" class="dot" data-go="{i}" aria-label="{i + 1}"></button>'
        for i in range(n)
    )
    controls = (
        f'<div class="controls"><button type="button" class="prev">{t("Anterior", "Back")}</button>'
        f'<div class="dots">{dots}</div>'
        f'<button type="button" class="play"><span class="idle">{t("Reproducir", "Play")}</span>'
        f'<span class="busy">{t("Pausa", "Pause")}</span></button>'
        f'<button type="button" class="next primary">{t("Siguiente", "Next")}</button></div>'
    )
    hidden = "" if first else " hidden"
    return (
        f'<section class="scenario" id="sc-{s.key}" role="tabpanel" aria-labelledby="tab-{s.key}"{hidden}>'
        f'{controls}<ol class="frames">{frames}</ol></section>'
    )


def _frame(f: Frame, i: int, n: int) -> str:
    when = f'<time datetime="{f.when.isoformat()}">{f.when:%H:%M:%S} UTC</time>' if f.when else ""
    step = t(f"Paso {i + 1} de {n}", f"Step {i + 1} of {n}")
    return (
        f'<li class="frame{" tone-" + f.tone if f.tone else ""}" data-step="{i}">'
        f'<header class="cap"><p class="meta">{step}{when}</p><h3>{f.title}</h3><p>{f.caption}</p></header>'
        f'<div class="stage">'
        f'<div class="pane"><h4 class="pane-h">{t("La tienda", "The shop")}</h4>{_shop(f.shop)}</div>'
        f'<div class="pane mind"><h4 class="pane-h">{t("Lo que ve y piensa Centinela", "What Centinela sees and thinks")}</h4>{f.mind}</div>'
        f'<div class="pane phone"><h4 class="pane-h">{t("Teléfono del ingeniero", "The engineer’s phone")}</h4>{f.phone}</div>'
        f"</div></li>"
    )
