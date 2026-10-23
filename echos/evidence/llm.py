"""Gemini client (official `google-genai` SDK) for the Evidence agent, plus a FakeLLM for tests.

The key is read from ECHOS_LLM_API_KEY (.env) and never printed or logged. `make_llm()` returns None when the switch is off, no key or no model is set;
the agent then writes the fixed-template card. Safety rails: hard per-call timeout, a requests-per-minute limiter, at most one retry on HTTP 429, and a
response cache in the database (echos/evidence/explain.py) so a replayed demo makes no second call. The LLM is never on the alert path."""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from echos.config import ROOT, load_config

ENV_KEY = "ECHOS_LLM_API_KEY"
log = logging.getLogger("echos.evidence.llm")


class LLMError(Exception):
    pass


class RateLimiter:
    def __init__(self, per_min: float):
        self.gap = 60.0 / max(per_min, 0.1)
        self.next = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            t = time.time()
            d = max(self.next - t, 0.0)
            self.next = max(t, self.next) + self.gap
        if d:
            time.sleep(d)


class GeminiLLM:
    def __init__(self, api_key: str, model: str, temperature: float = 0.1, timeout_s: float = 20.0, rpm: float = 10):
        from google import genai
        from google.genai import types
        self.model, self.temperature, self.timeout_s = model, temperature, float(timeout_s)
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(max(self.timeout_s, 10.0) * 1000)))
        self.limiter = RateLimiter(rpm)

    def _call(self, system: str, user: str) -> str:
        from google.genai import types
        r = self._client.models.generate_content(
            model=self.model, contents=user,
            config=types.GenerateContentConfig(system_instruction=system, temperature=self.temperature, response_mime_type="application/json"))
        return r.text or ""

    def generate(self, system: str, user: str) -> str:
        """One JSON-mode request under a hard timeout; one retry after a 429. Any failure raises LLMError (the caller falls back to the template)."""
        for attempt in (1, 2):
            self.limiter.wait()
            box: dict = {}

            def run():
                try:
                    box["out"] = self._call(system, user)
                except BaseException as ex:  # noqa: BLE001 - re-raised below
                    box["err"] = ex
            t = threading.Thread(target=run, daemon=True)
            t.start()
            t.join(self.timeout_s + 1.0)
            if t.is_alive():
                raise LLMError(f"no response within {self.timeout_s:g}s")
            if "err" not in box:
                return box["out"]
            ex = box["err"]
            code = getattr(ex, "code", None)
            if code == 429 and attempt == 1:
                time.sleep(min(5.0, 10.0))
                continue
            raise LLMError(f"{type(ex).__name__}: {str(ex)[:160]}") from ex
        raise LLMError("unreachable")


class FakeLLM:
    """Scripted responses (strings, dicts, exceptions or callables(system, user)); the last one repeats. Never touches the network."""
    def __init__(self, script=None, model: str = "fake-model"):
        self.script, self.model, self.calls, self.prompts = list(script or []), model, 0, []

    def generate(self, system: str, user: str) -> str:
        self.calls += 1
        self.prompts.append(user)
        r = self.script.pop(0) if len(self.script) > 1 else (self.script[0] if self.script else "{}")
        if callable(r):
            r = r(system, user)
        if isinstance(r, BaseException):
            raise r
        return r if isinstance(r, str) else json.dumps(r)


def make_llm(cfg: dict | None = None):
    cfg = cfg or load_config()
    ec = cfg.get("evidence") or {}
    model = os.environ.get("ECHOS_LLM_MODEL") or ec.get("model")
    if not ec.get("use_llm", True) or os.environ.get("ECHOS_EVIDENCE_LLM") == "off" or not model:
        return None
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key = os.environ.get(ENV_KEY)
    if not key:
        return None
    return GeminiLLM(key, model, ec.get("temperature", 0.1), ec.get("timeout_s", 20), ec.get("rpm", 10))
