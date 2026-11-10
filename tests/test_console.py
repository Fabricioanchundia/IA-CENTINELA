import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from centinela.actions.policy import check
from centinela.actions.store import InMemoryActionStore
from centinela.console.app import COOKIE, ConsoleConfig, create_app
from centinela.console.security import LoginThrottle, Sessions
from centinela.incidents.store import InMemoryIncidentStore
from tests.test_tracker import SERVICE_DOWN, T0

NOW = T0 + timedelta(minutes=3)
PASSWORD = "correct-horse-battery-staple"  # noqa: S105 - test fixture
CONFIG = ConsoleConfig("fabricio", PASSWORD, b"k" * 48)


@pytest.fixture
def world() -> tuple[TestClient, InMemoryIncidentStore, InMemoryActionStore, dict[str, datetime]]:
    incidents, actions = InMemoryIncidentStore(), InMemoryActionStore()
    incident_id = incidents.create((SERVICE_DOWN,), T0)
    incidents.add_event(
        incident_id,
        "diagnosis",
        {
            "diagnosis": {
                "summary": "payments <script>alert(1)</script> scaled to 0",
                "root_cause_service": "payments",
                "category": "service_down",
                "confidence": 0.95,
            },
            "agrees_with_baseline": True,
            "model": "qwen3:8b",
            "seconds": 5.1,
        },
        T0,
    )
    actions.propose(
        incident_id, check("scale_up", "payments"), "payments down", "centinela-agent", T0
    )
    clock = {"now": NOW}
    app = create_app(incidents, actions, CONFIG, clock=lambda: clock["now"])
    return TestClient(app, follow_redirects=False), incidents, actions, clock


def login(client: TestClient) -> str:
    resp = client.post("/login", data={"username": "fabricio", "password": PASSWORD})
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    page = client.get("/")
    match = re.search(r'name="csrf" value="([0-9a-f]+)"', page.text)
    assert match
    return match.group(1)


def test_pages_need_a_login_and_send_security_headers(world) -> None:  # type: ignore[no-untyped-def]
    client = world[0]
    resp = client.get("/")
    assert resp.status_code == 303 and resp.headers["location"] == "/login"
    login_page = client.get("/login")
    assert "script-src" not in login_page.headers["content-security-policy"]  # none allowed
    assert "default-src 'none'" in login_page.headers["content-security-policy"]
    assert login_page.headers["x-frame-options"] == "DENY"
    assert "<script" not in login_page.text
    assert client.get("/docs").status_code == 404  # no API explorer in production


def test_wrong_password_and_lockout(world) -> None:  # type: ignore[no-untyped-def]
    client = world[0]
    for _ in range(5):
        resp = client.post("/login", data={"username": "fabricio", "password": "guess"})
        assert "Wrong" in resp.headers["location"].replace("%20", " ")
    resp = client.post("/login", data={"username": "fabricio", "password": PASSWORD})
    assert "Too many failed attempts" in resp.headers["location"].replace("%20", " ")


def test_session_cookie_is_httponly_and_strict(world) -> None:  # type: ignore[no-untyped-def]
    client = world[0]
    resp = client.post("/login", data={"username": "fabricio", "password": PASSWORD})
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_dashboard_escapes_text_from_the_system(world) -> None:  # type: ignore[no-untyped-def]
    client = world[0]
    login(client)
    page = client.get("/").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "scale payments to 1 replica(s)" in page and "expires in 12 min" in page
    assert "the rule-based check agrees" in page


def test_approve_from_the_web_records_the_user(world) -> None:  # type: ignore[no-untyped-def]
    client, _, actions, _ = world
    csrf = login(client)
    resp = client.post("/actions/1/approve", data={"csrf": csrf, "note": "go"})
    assert "approved%20by%20fabricio" in resp.headers["location"]
    action = actions.get(1)
    assert action is not None and (action.status, action.decided_by, action.decision_note) == (
        "approved",
        "fabricio",
        "go",
    )
    again = client.post("/actions/1/approve", data={"csrf": csrf})
    assert "already%20approved" in again.headers["location"]


def test_reject_needs_a_reason(world) -> None:  # type: ignore[no-untyped-def]
    client, _, actions, _ = world
    csrf = login(client)
    resp = client.post("/actions/1/reject", data={"csrf": csrf, "note": " "})
    assert "Not%20recorded" in resp.headers["location"]
    client.post("/actions/1/reject", data={"csrf": csrf, "note": "it is a drill"})
    action = actions.get(1)
    assert action is not None and action.status == "rejected"


def test_forged_requests_are_refused(world) -> None:  # type: ignore[no-untyped-def]
    client, _, actions, _ = world
    login(client)
    assert client.post("/actions/1/approve", data={}).status_code == 403  # no CSRF token
    assert client.post("/actions/1/approve", data={"csrf": "0" * 64}).status_code == 403
    action = actions.get(1)
    assert action is not None and action.status == "pending"
    anonymous = TestClient(client.app, follow_redirects=False)
    assert anonymous.post("/actions/1/approve", data={"csrf": "x"}).headers["location"] == "/login"


def test_tampered_or_expired_sessions_are_rejected(world) -> None:  # type: ignore[no-untyped-def]
    client, _, _, clock = world
    login(client)
    token = client.cookies[COOKIE]
    user, expires, sig = token.split("|")
    client.cookies.clear()
    client.cookies.set(COOKIE, f"admin|{expires}|{sig}")
    assert client.get("/").status_code == 303  # signature no longer matches
    client.cookies.clear()
    client.cookies.set(COOKIE, token)
    assert client.get("/").status_code == 200
    clock["now"] = NOW + timedelta(hours=9)
    assert client.get("/").status_code == 303


def test_expired_action_cannot_be_approved_from_the_web(world) -> None:  # type: ignore[no-untyped-def]
    client, _, _, clock = world
    csrf = login(client)
    clock["now"] = T0 + timedelta(minutes=16)
    resp = client.post("/actions/1/approve", data={"csrf": csrf})
    assert "expired" in resp.headers["location"]


def test_logout_needs_csrf(world) -> None:  # type: ignore[no-untyped-def]
    client = world[0]
    csrf = login(client)
    assert client.post("/logout", data={}).status_code == 403
    resp = client.post("/logout", data={"csrf": csrf})
    assert resp.headers["location"].startswith("/login")


def test_security_primitives() -> None:
    sessions = Sessions(b"x" * 32)
    now = datetime(2026, 11, 9, tzinfo=UTC)
    token = sessions.issue("ana", now)
    assert sessions.user(token, now) == "ana"
    assert sessions.user(token + "0", now) is None
    assert sessions.user("garbage", now) is None
    assert Sessions(b"y" * 32).user(token, now) is None  # other key
    assert sessions.csrf_ok(token, sessions.csrf(token))
    assert not sessions.csrf_ok(token, None)
    throttle = LoginThrottle()
    for _ in range(5):
        throttle.fail("1.2.3.4", now)
    assert throttle.locked("1.2.3.4", now) and not throttle.locked("5.6.7.8", now)
    assert not throttle.locked("1.2.3.4", now + timedelta(minutes=16))
