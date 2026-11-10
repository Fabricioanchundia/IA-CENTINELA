"""Centinela approval console: see what the agent proposes, approve or reject it.

    centinela console          (in the cluster; locally: ./scripts/k8s.sh console)

It has no Kubernetes permissions at all and connects to PostgreSQL as `centinela_console`,
a role that can read and can only record decisions. Approving here and approving with the
CLI run the same code (actions/approvals.py). Server-rendered HTML, no JavaScript: the strict
Content-Security-Policy forbids scripts entirely.
"""

import html
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from centinela import __version__
from centinela.actions.approvals import ApprovalError, approve, reject
from centinela.actions.models import Action
from centinela.actions.store import ActionStore
from centinela.console.security import LoginThrottle, Sessions, password_ok
from centinela.incidents.models import Incident
from centinela.incidents.store import IncidentStore

COOKIE = "centinela_session"
CSP = (
    "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

CSS = (Path(__file__).parent / "console.css").read_text()


@dataclass(frozen=True)
class ConsoleConfig:
    user: str
    password: str
    secret_key: bytes
    secure_cookies: bool = False  # True behind HTTPS (Phase 10)


def e(value: object) -> str:
    """Escape everything that comes from the database: it may contain text from logs."""
    return html.escape("" if value is None else str(value), quote=True)


def _hms(at: datetime | None) -> str:
    return at.astimezone(UTC).strftime("%H:%M:%S UTC") if at else "-"


def page(title: str, body: str, refresh: bool = False) -> str:
    meta = '<meta http-equiv="refresh" content="15">' if refresh else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"{meta}<title>{e(title)}</title>"
        '<link rel="stylesheet" href="/static/console.css"></head>'
        f"<body>{body}</body></html>"
    )


def create_app(
    incidents: IncidentStore,
    actions: ActionStore,
    config: ConsoleConfig,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    app = FastAPI(
        title="Centinela console",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    sessions = Sessions(config.secret_key)
    throttle = LoginThrottle()

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[..., Any]) -> Response:
        response: Response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    def current_user(request: Request) -> str | None:
        return sessions.user(request.cookies.get(COOKIE), clock())

    def to(path: str, msg: str | None = None) -> RedirectResponse:
        url = path + (f"?msg={quote(msg[:200])}" if msg else "")
        return RedirectResponse(url, status_code=303)

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        return "ok"

    @app.get("/static/console.css")
    def css() -> Response:
        return Response(CSS, media_type="text/css")

    # -- login ----------------------------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    def login_form(msg: str | None = None) -> str:
        flash = f'<p class="flash bad">{e(msg)}</p>' if msg else ""
        return page(
            "Sign in · Centinela",
            '<main class="login"><div class="card"><h1 class="big">Centinela console</h1>'
            '<p class="muted">Approve or reject the fixes proposed by the AI agent.</p>'
            f'{flash}<form method="post" action="/login">'
            '<label for="u">User</label><input id="u" name="username" autocomplete="username" '
            'required maxlength="64">'
            '<label for="p">Password</label><input id="p" name="password" type="password" '
            'autocomplete="current-password" required maxlength="256">'
            '<button class="approve" type="submit">Sign in</button></form></div></main>',
        )

    @app.post("/login")
    def login(
        request: Request,
        username: Annotated[str, Form(max_length=64)],
        password: Annotated[str, Form(max_length=256)],
    ) -> Response:
        now = clock()
        client = request.client.host if request.client else "unknown"
        if throttle.locked(client, now):
            return to("/login", "Too many failed attempts. Try again in 15 minutes.")
        user_ok = password_ok(username, config.user)
        if not (password_ok(password, config.password) and user_ok):
            throttle.fail(client, now)
            return to("/login", "Wrong user or password.")
        throttle.reset(client)
        response = to("/")
        response.set_cookie(
            COOKIE,
            sessions.issue(config.user, now),
            httponly=True,
            samesite="strict",
            secure=config.secure_cookies,
            max_age=8 * 3600,
        )
        return response

    @app.post("/logout")
    def logout(request: Request, csrf: Annotated[str, Form()] = "") -> Response:
        if not sessions.csrf_ok(request.cookies.get(COOKIE), csrf):
            return PlainTextResponse("invalid request", status_code=403)
        response = to("/login", "Signed out.")
        response.delete_cookie(COOKIE)
        return response

    # -- dashboard ------------------------------------------------------------------------
    @app.get("/", response_model=None)
    def dashboard(request: Request, msg: str | None = None) -> Response:
        user = current_user(request)
        if user is None:
            return to("/login")
        token = request.cookies.get(COOKIE, "")
        csrf = sessions.csrf(token)
        now = clock()
        pending = [a for a in actions.recent(50, open_only=True) if a.status == "pending"]
        open_incidents = [i for i in incidents.recent(50) if i.status == "open"]
        history = [a for a in actions.recent(10) if a.status != "pending"]

        body = [
            '<header><h1>Centinela · approvals</h1><div class="row">'
            f'<span class="muted">signed in as <b>{e(user)}</b></span>'
            f'<form class="inline" method="post" action="/logout">'
            f'<input type="hidden" name="csrf" value="{e(csrf)}">'
            '<button type="submit">Sign out</button></form></div></header><main>'
        ]
        if msg:
            body.append(f'<p class="flash">{e(msg)}</p>')
        body.append(f"<h2>Waiting for your decision ({len(pending)})</h2>")
        if not pending:
            body.append(
                '<div class="card muted">Nothing to decide. The agent proposes a fix '
                "after it diagnoses a new incident.</div>"
            )
        for action in pending:
            body.append(_action_card(action, incidents, csrf, now))
        body.append(f"<h2>Open incidents ({len(open_incidents)})</h2>")
        body.append(_incidents_table(open_incidents))
        body.append("<h2>Recent decisions</h2>")
        body.append(_history_table(history))
        body.append(
            '<p class="muted">Refreshes every 15 s. Actions expire 15 minutes after the '
            "proposal. Approving here is the same as <code>centinela approve</code>: the "
            "executor runs it and verifies the result.</p></main>"
        )
        return HTMLResponse(page("Centinela · approvals", "".join(body), refresh=True))

    def decide(
        request: Request, action_id: int, csrf: str, run: Callable[[str], Action]
    ) -> Response:
        user = current_user(request)
        if user is None:
            return to("/login")
        if not sessions.csrf_ok(request.cookies.get(COOKIE), csrf):
            return PlainTextResponse("invalid request (CSRF token)", status_code=403)
        try:
            action = run(user)
        except ApprovalError as exc:
            return to("/", f"Not recorded: {exc}")
        return to("/", f"Action {action.id} {action.status} by {action.decided_by}.")

    @app.post("/actions/{action_id}/approve")
    def approve_action(
        request: Request,
        action_id: int,
        csrf: Annotated[str, Form()] = "",
        note: Annotated[str, Form(max_length=500)] = "",
    ) -> Response:
        return decide(
            request,
            action_id,
            csrf,
            lambda user: approve(
                actions, action_id, user, clock(), note.strip() or "approved in the console"
            ),
        )

    @app.post("/actions/{action_id}/reject")
    def reject_action(
        request: Request,
        action_id: int,
        csrf: Annotated[str, Form()] = "",
        note: Annotated[str, Form(max_length=500)] = "",
    ) -> Response:
        return decide(
            request, action_id, csrf, lambda user: reject(actions, action_id, user, note, clock())
        )

    return app


def _latest_diagnosis(incidents: IncidentStore, incident_id: int) -> dict[str, Any] | None:
    found = [ev["payload"] for ev in incidents.events(incident_id) if ev["kind"] == "diagnosis"]
    return found[-1] if found else None


def _action_card(action: Action, incidents: IncidentStore, csrf: str, now: datetime) -> str:
    incident = incidents.get(action.incident_id)
    report = _latest_diagnosis(incidents, action.incident_id)
    left = max(0, int((action.expires_at - now).total_seconds() // 60))
    diag = ""
    if report:
        d = report["diagnosis"]
        agree = (
            '<span class="ok">the rule-based check agrees</span>'
            if report.get("agrees_with_baseline")
            else '<span class="warn">the rule-based check blames '
            f"{e(report.get('baseline_service'))}</span>"
        )
        diag = (
            f'<p><b>AI diagnosis:</b> {e(d.get("summary"))}<br><span class="muted">root cause '
            f"<b>{e(d.get('root_cause_service'))}</b> ({e(d.get('category'))}), confidence "
            f"{float(d.get('confidence', 0)):.2f} · {agree} · model {e(report.get('model'))}, "
            f"{e(report.get('seconds'))} s</span></p>"
        )
    title = e(incident.title) if incident else f"incident {action.incident_id}"
    hidden = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    return (
        '<div class="card"><div class="row"><div class="grow">'
        f'<div class="muted">Incident #{action.incident_id} · {title}</div>'
        f'<div class="big">{e(action.describe())}</div>{diag}'
        f'<p class="muted">Proposed by {e(action.proposed_by)} at {_hms(action.proposed_at)} · '
        f'<span class="warn">expires in {left} min</span></p></div>'
        '<div class="grow">'
        f'<form method="post" action="/actions/{action.id}/approve">{hidden}'
        '<input name="note" maxlength="500" placeholder="Note (optional)">'
        '<p><button class="approve" type="submit">'
        f"Approve: {e(action.describe())}</button></p></form>"
        f'<form method="post" action="/actions/{action.id}/reject">{hidden}'
        '<textarea name="note" rows="2" maxlength="500" required '
        'placeholder="Why do you reject it? (required, goes into the postmortem)"></textarea>'
        '<p><button class="reject" type="submit">Reject</button></p></form></div></div></div>'
    )


def _incidents_table(rows: list[Incident]) -> str:
    if not rows:
        return '<div class="card muted">No open incidents. All quiet.</div>'
    cells = "".join(
        f"<tr><td>#{i.id}</td><td>{e(i.severity)}</td><td>{e(i.title)}</td>"
        f"<td>{e(i.service)}</td><td>{_hms(i.started_at)}</td>"
        f"<td>{i.mttd_seconds:.0f} s</td></tr>"
        for i in rows
    )
    return (
        "<table><tr><th>ID</th><th>Severity</th><th>Title</th><th>Service</th>"
        f"<th>Started</th><th>MTTD</th></tr>{cells}</table>"
    )


def _history_table(rows: list[Action]) -> str:
    if not rows:
        return '<div class="card muted">No decisions yet.</div>'
    css = {"executed": "ok", "failed": "bad", "rejected": "warn", "expired": "warn"}
    cells = "".join(
        f'<tr><td>#{a.id}</td><td class="{css.get(a.status, "")}">{e(a.status)}</td>'
        f"<td>{e(a.describe())}</td><td>{e(a.decided_by or '-')}</td>"
        f"<td>{e(a.decision_note or '')}</td><td>{e(a.result or '')}</td></tr>"
        for a in rows
    )
    return (
        "<table><tr><th>ID</th><th>Status</th><th>Action</th><th>Decided by</th>"
        f"<th>Note</th><th>Result</th></tr>{cells}</table>"
    )
