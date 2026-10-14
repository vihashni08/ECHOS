"""Signed, expiring "I have seen this" link for alert e-mails.
Token = base64url(alert_id) . expiry_unix . HMAC-SHA256(secret, alert_id + "|" + expiry) (hex, 32 chars). Only the alert id and the expiry are in the link
(no names, no readings). The secret comes from the environment (ECHOS_LINK_SECRET) and the base URL from ECHOS_PUBLIC_URL; if either is missing
`ack_url()` returns None and the mail simply has no link (never a broken one). Stateless: acknowledging again is harmless (lifecycle.acknowledge is idempotent)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Optional

SECRET_ENV, URL_ENV = "ECHOS_LINK_SECRET", "ECHOS_PUBLIC_URL"
MIN_SECRET = 16
DEFAULT_TTL_S = 86400.0


class LinkError(Exception):
    """kind: 'invalid' (malformed / forged / wrong secret) | 'expired' | 'unconfigured'."""
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _unb64(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def _secret() -> Optional[bytes]:
    s = os.environ.get(SECRET_ENV, "")
    return s.encode() if len(s) >= MIN_SECRET else None


def _sig(secret: bytes, alert_id: str, exp: int) -> str:
    return hmac.new(secret, f"{alert_id}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def make_token(alert_id: str, ttl_s: float = DEFAULT_TTL_S, now: Optional[float] = None) -> Optional[str]:
    secret = _secret()
    if secret is None:
        return None
    exp = int((time.time() if now is None else now) + ttl_s)
    return f"{_b64(alert_id)}.{exp}.{_sig(secret, alert_id, exp)}"


def verify_token(token: str, now: Optional[float] = None) -> str:
    """-> the alert id; raises LinkError."""
    secret = _secret()
    if secret is None:
        raise LinkError("unconfigured")
    try:
        a, e, s = token.split(".")
        alert_id, exp = _unb64(a), int(e)
    except Exception:  # noqa: BLE001 - any malformed token is just invalid
        raise LinkError("invalid") from None
    if not hmac.compare_digest(s.encode(), _sig(secret, alert_id, exp).encode()):
        raise LinkError("invalid")
    if (time.time() if now is None else now) > exp:
        raise LinkError("expired")
    return alert_id


def ack_url(alert_id: str, ttl_s: float = DEFAULT_TTL_S, now: Optional[float] = None) -> Optional[str]:
    base = os.environ.get(URL_ENV, "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return None
    tok = make_token(alert_id, ttl_s, now)
    return f"{base}/api/ack/{tok}" if tok else None
