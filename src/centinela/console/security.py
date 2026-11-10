"""Sessions, CSRF tokens and login throttling, standard library only.

Session cookie = "user|expires|hmac". The HMAC key comes from a Terraform-generated secret, so
a cookie cannot be forged or extended. The CSRF token is a second HMAC derived from the
session, put in every form: another site can make the browser send the cookie (it cannot,
with SameSite=Strict, but defence in depth) yet it can never read or guess the token.
"""

import hashlib
import hmac
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

SESSION_TTL = timedelta(hours=8)
MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=15)


def _sign(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Sessions:
    key: bytes

    def issue(self, user: str, now: datetime) -> str:
        payload = f"{user}|{int((now + SESSION_TTL).timestamp())}"
        return f"{payload}|{_sign(self.key, payload)}"

    def user(self, token: str | None, now: datetime) -> str | None:
        if not token or token.count("|") != 2:
            return None
        user, expires, signature = token.split("|")
        if not hmac.compare_digest(signature, _sign(self.key, f"{user}|{expires}")):
            return None
        if not expires.isdigit() or int(expires) < now.timestamp():
            return None
        return user

    def csrf(self, token: str) -> str:
        return _sign(self.key, f"csrf|{token}")

    def csrf_ok(self, token: str | None, submitted: str | None) -> bool:
        return bool(token and submitted) and hmac.compare_digest(
            self.csrf(token or ""), submitted or ""
        )


def password_ok(given: str, expected: str) -> bool:
    """Constant-time comparison: the response time does not leak how much matched."""
    return hmac.compare_digest(given.encode(), expected.encode())


@dataclass
class LoginThrottle:
    """At most MAX_FAILURES failed logins per client in LOCKOUT, then locked for LOCKOUT."""

    failures: dict[str, deque[datetime]] = field(default_factory=dict)

    def locked(self, client: str, now: datetime) -> bool:
        recent = self.failures.get(client, deque())
        while recent and now - recent[0] > LOCKOUT:
            recent.popleft()
        return len(recent) >= MAX_FAILURES

    def fail(self, client: str, now: datetime) -> None:
        self.failures.setdefault(client, deque()).append(now)

    def reset(self, client: str) -> None:
        self.failures.pop(client, None)
