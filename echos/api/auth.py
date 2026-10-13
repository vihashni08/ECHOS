"""Optional authentication and roles. Off by default (local demo); on with `api.auth.enabled: true` or ECHOS_AUTH=on.
Tokens come from the environment only (ECHOS_TOKEN_CAREGIVER / _CLINICIAN / _OPERATOR); a role without a token cannot log in. Nothing is stored or printed here.
A request carries `Authorization: Bearer <token>` (the event stream, which cannot set headers, uses ?token=). Devices use their own `X-Echos-Token` on /api/ingest/events.
Roles: caregiver < clinician < operator."""
from __future__ import annotations

import hmac
import os
import re
import time
from collections import defaultdict, deque
from typing import Optional

LEVEL = {"caregiver": 1, "clinician": 2, "operator": 3}
ENV = {"caregiver": "ECHOS_TOKEN_CAREGIVER", "clinician": "ECHOS_TOKEN_CLINICIAN", "operator": "ECHOS_TOKEN_OPERATOR"}
PUBLIC = (("POST", re.compile(r"^/api/auth/login$")), ("GET", re.compile(r"^/api/auth/me$")), ("POST", re.compile(r"^/api/ingest/events$")))
# (method, path pattern, minimum role): first match wins; anything not listed is operator-only for POST and caregiver for GET
RULES = [
    ("POST", re.compile(r"^/api/alerts/.+/(ack|resolve)$"), "caregiver"),
    ("POST", re.compile(r"^/api/tasks/\d+/done$"), "caregiver"),
    ("POST", re.compile(r"^/api/patients/[^/]+/reports/weekly/(build|send)$"), "clinician"),
    ("POST", re.compile(r"^/api/outbox/\d+/retry$"), "clinician"),
    ("GET", re.compile(r"^/api/patients/[^/]+/reports/weekly"), "clinician"),
    ("GET", re.compile(r"^/api/outbox"), "clinician"),
    ("GET", re.compile(r"^/api/system/"), "operator"),
    ("GET", re.compile(r"^/api/agents/health$"), "operator"),
]


def enabled(cfg: dict) -> bool:
    return bool((cfg.get("api", {}).get("auth") or {}).get("enabled")) or os.environ.get("ECHOS_AUTH") == "on"


def role_of(token: Optional[str]) -> Optional[str]:
    """Constant-time comparison against every configured token."""
    if not token:
        return None
    found = None
    for role, var in ENV.items():
        want = os.environ.get(var)
        if want and hmac.compare_digest(token.encode(), want.encode()):
            found = role
    return found


def required_role(method: str, path: str) -> str:
    for m, pat, role in RULES:
        if m == method and pat.search(path):
            return role
    return "operator" if method not in ("GET", "HEAD", "OPTIONS") else "caregiver"


def is_public(method: str, path: str) -> bool:
    return any(m == method and p.search(path) for m, p in PUBLIC) or method == "OPTIONS" or not path.startswith("/api/")


def allowed(role: Optional[str], method: str, path: str) -> tuple[int, str]:
    """-> (status, message): 200 ok, 401 not logged in, 403 role too low."""
    if is_public(method, path):
        return 200, ""
    if role is None:
        return 401, "log in first (Authorization: Bearer <token>)"
    need = required_role(method, path)
    if LEVEL[role] < LEVEL[need]:
        return 403, f"this needs the {need} role (you are {role})"
    return 200, ""


class LoginLimiter:
    """At most `n` failed logins per `window_s` per client."""
    def __init__(self, n: int = 8, window_s: float = 60.0):
        self.n, self.w, self.fails = n, window_s, defaultdict(deque)

    def blocked(self, who: str) -> bool:
        q, now = self.fails[who], time.time()
        while q and now - q[0] > self.w:
            q.popleft()
        return len(q) >= self.n

    def fail(self, who: str) -> None:
        self.fails[who].append(time.time())
