"""Optional Gemini client (official `google-genai` SDK). Returns None when no key or no model is configured:
the Report agent then runs in template mode. The key is read from the env var ECHOS_LLM_API_KEY (via .env) and is
never printed or logged.

Safety rails (config.yaml `llm:`): hard per-call timeout (`timeout_s`, enforced by the SDK HTTP timeout AND a watchdog
thread; the Gemini API rejects HTTP deadlines under 10 s, so shorter values rely on the watchdog), a request-rate limiter (`max_requests_per_min`), and a dedicated exception type for timeouts so callers never
retry them. Reports call this off the alert path (see ReportAgent)."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

from echos.config import ROOT, load_config

ENV_KEY = "ECHOS_LLM_API_KEY"
log = logging.getLogger("echos.llm")


class LLMTimeout(TimeoutError):
    """The call exceeded the hard per-call timeout. Never retried."""


class LLMAPIError(Exception):
    """API error with the HTTP status code and error status (e.g. 429 RESOURCE_EXHAUSTED)."""
    def __init__(self, code, status, message, retry_after=None):
        self.code, self.status, self.retry_after = code, status, retry_after
        self.message = re.sub(r"AIza[\w-]+", "<key>", str(message))[:300]
        super().__init__(f"{code} {status}: {self.message}")


RETRY_CAP_S = 20.0


def _retry_delay(ex) -> float | None:
    """Delay suggested by the API (RetryInfo.retryDelay '12s', or 'Please retry in 12.3s' in the message)."""
    d = getattr(ex, "details", None)
    try:
        for item in (d.get("error", d) or {}).get("details", []):
            if str(item.get("@type", "")).endswith("RetryInfo"):
                return float(str(item["retryDelay"]).rstrip("s"))
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"retry in ([\d.]+)\s*s", f"{ex} {getattr(ex, 'message', '')}", re.I)
    return float(m.group(1)) if m else None


def _wrap(ex: BaseException) -> BaseException:
    code = getattr(ex, "code", None)
    if isinstance(code, int) and not isinstance(ex, TimeoutError):
        return LLMAPIError(code, getattr(ex, "status", None) or "UNKNOWN", getattr(ex, "message", None) or ex, _retry_delay(ex))
    return ex


def _is_timeout(ex: BaseException) -> bool:
    return isinstance(ex, TimeoutError) or "timeout" in type(ex).__name__.lower() or "timed out" in str(ex).lower()


@dataclass
class Step:
    """One model response: final `text`, or `calls` = [{"name", "args"}] (function calling). `raw` is the provider's own content
    object, kept so the next request can replay it (Gemini needs the original function-call parts back)."""
    text: str = ""
    calls: list = field(default_factory=list)
    raw: object = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None


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
        from echos.offline import llm_off
        if llm_off():
            raise RuntimeError("ECHOS_REPORT_LLM=off: refusing to build a Gemini client")
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

    def _guard(self, fn):
        """Run fn() under the hard timeout (SDK HTTP timeout + watchdog thread); API errors become LLMAPIError, timeouts LLMTimeout."""
        box: dict = {}

        def run():
            try:
                box["out"] = fn()
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
            raise _wrap(box["err"])
        return box["out"]

    def _once(self, system: str, user: str) -> str:
        self.limiter.wait()
        return self._guard(lambda: self._call(system, user))

    # ---- function-calling interface (used by the report agent through llm_budget; rate limiting is done there)
    def _contents(self, messages: list):
        from google.genai import types
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append(types.Content(role="user", parts=[types.Part(text=m["text"])]))
            elif m["role"] == "model":
                out.append(m.get("raw") or types.Content(role="model", parts=[types.Part.from_function_call(name=c["name"], args=c["args"]) for c in m["calls"]]))
            else:   # tool results go back as a user turn with function_response parts
                out.append(types.Content(role="user", parts=[types.Part.from_function_response(name=r["name"], response=r["response"]) for r in m["results"]]))
        return out

    def step(self, system: str, messages: list, tools: list | None = None, schema: dict | None = None) -> Step:
        """ONE request. tools=None -> final answer (JSON mime type + response_json_schema = `schema`, default DailyReport, no function calling); else manual function calling
        (automatic_function_calling disabled: the agent executes tools itself and enforces the step limit)."""
        from google.genai import types
        if tools:
            cfg = types.GenerateContentConfig(
                system_instruction=system, temperature=self.temperature,
                tools=[types.Tool(function_declarations=[types.FunctionDeclaration(name=t["name"], description=t["description"],
                                                                                   parameters_json_schema=t["parameters"]) for t in tools])],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        else:
            from echos.reports.schema import json_schema     # structured output: the same DailyReport model the prompt and the validator use
            cfg = types.GenerateContentConfig(system_instruction=system, temperature=self.temperature, response_mime_type="application/json",
                                              response_json_schema=schema or json_schema())

        def call():
            return self._client.models.generate_content(model=self.model, contents=self._contents(messages), config=cfg)
        r = self._guard(call)
        cand = (r.candidates or [None])[0]
        content = getattr(cand, "content", None)
        parts = (content.parts if content is not None else None) or []
        calls = [{"name": p.function_call.name, "args": dict(p.function_call.args or {})} for p in parts if getattr(p, "function_call", None)]
        text = "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))
        u = r.usage_metadata
        return Step(text=text, calls=calls, raw=content if calls else None, prompt_tokens=getattr(u, "prompt_token_count", None),
                    output_tokens=getattr(u, "candidates_token_count", None))

    def generate(self, system: str, user: str) -> str:
        """One request; on HTTP 429 exactly one retry after the API-suggested delay (capped at RETRY_CAP_S).
        Timeouts are never retried. Errors carry the HTTP code and error status (LLMAPIError)."""
        try:
            return self._once(system, user)
        except LLMAPIError as ex:
            if ex.code != 429:
                raise
            delay = min(ex.retry_after if ex.retry_after is not None else 5.0, RETRY_CAP_S)
            log.info("429 %s: retrying once after %.1fs", ex.status, delay)
            time.sleep(delay)
            return self._once(system, user)


def make_llm(cfg: dict | None = None):
    cfg = cfg or load_config()
    lc = cfg.get("llm") or {}
    model = lc.get("model")
    if not model or os.environ.get("ECHOS_REPORT_LLM") == "off":
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
