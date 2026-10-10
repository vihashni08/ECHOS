"""Optional Gemini client (official `google-genai` SDK). Returns None when no key or no model is configured:
the Report agent then runs in template mode. The key is read from the env var ECHOS_LLM_API_KEY (via .env) and is
never printed or logged.

Safety rails (config.yaml `llm:`): hard per-call timeout (`timeout_s`, enforced by the SDK HTTP timeout AND a watchdog
thread; the Gemini API rejects HTTP deadlines under 10 s, so shorter values rely on the watchdog), a request-rate limiter (`max_requests_per_min`), and a dedicated exception type for timeouts so callers never
retry them. Reports call this off the alert path (see ReportAgent)."""
from __future__ import annotations

import logging
import os
import threading
import time

from echos.config import ROOT, load_config

ENV_KEY = "ECHOS_LLM_API_KEY"
log = logging.getLogger("echos.llm")


class LLMTimeout(TimeoutError):
    """The call exceeded the hard per-call timeout. Never retried."""


def _is_timeout(ex: BaseException) -> bool:
    return isinstance(ex, TimeoutError) or "timeout" in type(ex).__name__.lower() or "timed out" in str(ex).lower()


class RateLimiter:
    """Minimum spacing between requests (thread-safe): at most `per_min` calls per minute."""
    def __init__(self, per_min: float):
        self.gap = 60.0 / per_min if per_min and per_min > 0 else 0.0
        self._next, self._lock = 0.0, threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self.gap
        if slot > now:
            time.sleep(slot - now)


class GeminiLLM:
    def __init__(self, api_key: str, model: str, temperature: float = 0.2, timeout_s: float = 15.0,
                 max_requests_per_min: float = 20):
        from google import genai
        from google.genai import types
        self.timeout_s = float(timeout_s)
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(max(self.timeout_s, 10.0) * 1000)))  # API rejects deadlines < 10 s
        self.model, self.temperature = model, temperature
        self.limiter = RateLimiter(max_requests_per_min)

    def _call(self, system: str, user: str) -> str:
        from google.genai import types
        r = self._client.models.generate_content(
            model=self.model, contents=user,
            config=types.GenerateContentConfig(system_instruction=system, temperature=self.temperature,
                                               response_mime_type="application/json"))
        return r.text or ""

    def generate(self, system: str, user: str) -> str:
        self.limiter.wait()
        box: dict = {}

        def run():
            try:
                box["out"] = self._call(system, user)
            except BaseException as ex:  # noqa: BLE001 - re-raised in the caller
                box["err"] = ex
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(self.timeout_s + (1.0 if self.timeout_s >= 10 else 0.0))  # watchdog; below 10 s it is the only enforcement
        if t.is_alive():
            raise LLMTimeout(f"no response within {self.timeout_s:g}s")
        if "err" in box:
            if _is_timeout(box["err"]):
                raise LLMTimeout(f"{type(box['err']).__name__}: timed out after {self.timeout_s:g}s") from box["err"]
            raise box["err"]
        return box["out"]


def make_llm(cfg: dict | None = None):
    cfg = cfg or load_config()
    lc = cfg.get("llm") or {}
    model = lc.get("model")
    if not model:
        return None
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key = os.environ.get(ENV_KEY)
    if not key:
        return None
    return GeminiLLM(key, model, lc.get("temperature", 0.2), lc.get("timeout_s", 15), lc.get("max_requests_per_min", 20))
