"""LLM budget manager for the Report agent. Strict order for every report:

    cache  ->  primary model  ->  fallback model  ->  template

* Model names and requests-per-minute limits come from config.yaml (`llm.model`, `llm.fallback_model`, `llm.rpm`). Each model gets a sliding-window
  rate limiter set RATE_MARGIN (20%) below its limit.
* HTTP 429: that model goes into a 60 s cooldown and the next model is tried IMMEDIATELY (no retry on the same model, no sleeping).
  While a model is cooling down it is skipped without a request.
* Timeouts (hard 45 s, config `llm.timeout_s`) are never retried on the same model. Other transient errors (5xx, network, bad JSON) get at
  most ONE retry on the same model; client errors (400/401/403/404) are not retried at all. Both then move on to the next model.
* A report whose output keeps failing the validator is NOT sent to the fallback model: the attempt raises GenerationFailed and the caller uses the template.
* Responses are cached in SQLite, keyed by a hash of (prompt version, model-independent structured input); only validated output is stored.
* Every call, cache hit, cooldown and fallback reason is logged with its HTTP status (python logger `echos.llm.budget`, table `llm_log`
  in the cache database, and the per-report event list returned to the caller as provenance).
Nothing here sits on the alert path: reports run in a background worker (see ReportAgent)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from echos.config import ROOT, load_config
from echos.reports.llm import LLMAPIError, Step

log = logging.getLogger("echos.llm.budget")
RATE_MARGIN = 0.20          # stay this far below each model's requests-per-minute limit
COOLDOWN_S = 60.0           # after an HTTP 429
NO_RETRY_CODES = {400, 401, 403, 404}


class GenerationFailed(Exception):
    """The model answered but the output is unusable (validator failed twice, tool misuse ...): go to the template, do NOT try another model."""
    def __init__(self, reason: str, validation: Optional[dict] = None):
        super().__init__(reason)
        self.reason, self.validation = reason, validation


class SlidingLimiter:
    """At most floor(rpm * (1 - margin)) requests in any 60 s window (min 1). rpm None/0 = unlimited. Thread-safe; clock and sleep injectable."""
    def __init__(self, rpm: Optional[float], margin: float = RATE_MARGIN, clock=time.monotonic, sleep=time.sleep):
        self.cap = max(1, int(rpm * (1 - margin))) if rpm else 0
        self.clock, self.sleep, self._ts, self._lock = clock, sleep, deque(), threading.Lock()

    def wait(self) -> float:
        """Block until a request may go out; returns the seconds waited."""
        if not self.cap:
            return 0.0
        waited = 0.0
        while True:
            with self._lock:
                now = self.clock()
                while self._ts and now - self._ts[0] >= 60.0:
                    self._ts.popleft()
                if len(self._ts) < self.cap:
                    self._ts.append(now)
                    return waited
                delay = 60.0 - (now - self._ts[0]) + 0.01
            self.sleep(delay)
            waited += delay


class Cache:
    """SQLite response cache + call log. One short-lived connection per operation (safe across the report worker threads)."""
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if self.path == ":memory:" else None
        self._lock = threading.Lock()
        self._run("CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, prompt_version TEXT, model TEXT, value TEXT, created_ts REAL)")
        self._run("CREATE TABLE IF NOT EXISTS llm_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, event TEXT, model TEXT, http_status TEXT, detail TEXT)")

    def _run(self, sql, args=()):
        with self._lock:
            c = self._mem if self._mem is not None else sqlite3.connect(self.path, timeout=30)
            try:
                rows = c.execute(sql, args).fetchall()
                c.commit()
                return rows
            finally:
                if self._mem is None:
                    c.close()

    def get(self, key: str) -> Optional[dict]:
        r = self._run("SELECT value, model FROM llm_cache WHERE key=?", (key,))
        return {**json.loads(r[0][0]), "_model": r[0][1]} if r else None

    def put(self, key: str, prompt_version: str, model: str, value: dict) -> None:
        self._run("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?)", (key, prompt_version, model, json.dumps(value, default=str), time.time()))

    def log(self, event: str, model: str, status, detail: str) -> None:
        try:
            self._run("INSERT INTO llm_log (ts, event, model, http_status, detail) VALUES (?,?,?,?,?)", (time.time(), event, model, str(status), detail[:300]))
        except sqlite3.Error:   # logging must never break a report
            pass


@dataclass
class Backend:
    """One model: a client with `.step(system, messages, tools) -> Step`, its name and requests-per-minute limit."""
    name: str
    client: Any
    rpm: Optional[float] = None
    limiter: Optional[SlidingLimiter] = None
    requests: int = 0

    def __post_init__(self):
        self.limiter = self.limiter or SlidingLimiter(self.rpm)


@dataclass
class RunResult:
    ok: bool
    value: Optional[dict] = None
    model: Optional[str] = None            # model that produced `value` (for a cache hit: the model that originally produced it)
    cache_hit: bool = False
    fallback_reason: Optional[str] = None
    validation: Optional[dict] = None      # validator result of a rejected output (GenerationFailed)
    requests: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    models_tried: list = field(default_factory=list)
    events: list = field(default_factory=list)


def make_key(prompt_version: str, structured_input) -> str:
    """Hash of (prompt version, model-independent structured input): the model name is deliberately NOT part of the key."""
    blob = json.dumps({"v": prompt_version, "in": structured_input}, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


class RunContext:
    """Handed to the attempt function: sends ONE request through the backend's limiter, counts requests / tokens, logs the call."""
    def __init__(self, budget: "LLMBudget", backend: Backend, res: RunResult):
        self.budget, self.backend, self.res = budget, backend, res

    @property
    def model(self) -> str:
        return self.backend.name

    def step(self, system: str, messages: list, tools: Optional[list] = None) -> Step:
        b = self.backend
        waited = b.limiter.wait()
        t0 = time.monotonic()
        try:
            r = b.client.step(system, messages, tools)
        except Exception as ex:   # noqa: BLE001 - logged with its HTTP status, then handled by LLMBudget.run
            b.requests += 1
            self.res.requests += 1
            code = ex.code if isinstance(ex, LLMAPIError) else ("timeout" if isinstance(ex, TimeoutError) else type(ex).__name__)
            self.budget._event(self.res, "call_failed", b.name, code, f"{type(ex).__name__}: {str(ex)[:120]}")
            raise
        b.requests += 1
        self.res.requests += 1
        self.res.prompt_tokens += r.prompt_tokens or 0
        self.res.output_tokens += r.output_tokens or 0
        self.budget._event(self.res, "call", b.name, 200, f"{'tool_calls=' + str(len(r.calls)) if r.calls else 'text'} "
                           f"wait={waited:.1f}s dt={time.monotonic() - t0:.1f}s")
        return r


class LLMBudget:
    def __init__(self, backends: list[Backend], cache: Optional[Cache] = None, clock=time.monotonic, cooldown_s: float = COOLDOWN_S):
        self.backends, self.cache, self.clock, self.cooldown_s = list(backends), cache, clock, cooldown_s
        self._cool: dict[str, float] = {}
        self._lock = threading.Lock()

    # ---- cooldown
    def cooling(self, name: str) -> float:
        """Seconds of cooldown left for a model (0 = available)."""
        with self._lock:
            return max(0.0, self._cool.get(name, 0.0) - self.clock())

    def _start_cooldown(self, name: str) -> None:
        with self._lock:
            self._cool[name] = self.clock() + self.cooldown_s

    def _event(self, res: RunResult, kind: str, model: str, status, detail: str) -> None:
        res.events.append({"event": kind, "model": model, "http_status": str(status), "detail": detail[:200]})
        log.info("%s model=%s http=%s %s", kind, model, status, detail[:200])
        if self.cache:
            self.cache.log(kind, model, status, detail)

    # ---- main entry
    def run(self, attempt: Callable[[RunContext], dict], key: Optional[str] = None, revalidate: Optional[Callable[[dict], bool]] = None,
            prompt_version: str = "") -> RunResult:
        """cache -> primary -> fallback. `attempt(ctx)` returns the VALIDATED value (a JSON-able dict) or raises GenerationFailed / API errors.
        `revalidate(cached_value)` re-checks a cached value; a failing entry is ignored. No model successful -> RunResult(ok=False, fallback_reason)."""
        res = RunResult(ok=False)
        if key is not None and self.cache:
            hit = self.cache.get(key)
            if hit is not None:
                model = hit.pop("_model", None)
                if revalidate is None or revalidate(hit):
                    self._event(res, "cache_hit", model or "-", "-", f"key={key[:12]}")
                    return RunResult(ok=True, value=hit, model=model, cache_hit=True, events=res.events)
                self._event(res, "cache_invalid", model or "-", "-", f"key={key[:12]} failed re-validation; ignored")
        reasons = []
        for b in self.backends:
            left = self.cooling(b.name)
            if left > 0:
                self._event(res, "skipped_cooldown", b.name, 429, f"cooling down, {left:.0f}s left")
                reasons.append(f"{b.name}: cooldown")
                continue
            res.models_tried.append(b.name)
            ctx, tries = RunContext(self, b, res), 0
            while True:
                try:
                    value = attempt(ctx)
                except GenerationFailed as ex:
                    self._event(res, "generation_failed", b.name, "-", ex.reason)
                    res.fallback_reason, res.validation = f"{b.name}: {ex.reason}", ex.validation
                    return res            # template; the fallback model would not fix an output the validator rejects
                except LLMAPIError as ex:
                    if ex.code == 429:
                        self._start_cooldown(b.name)
                        self._event(res, "cooldown_start", b.name, 429, f"{ex.status}; {self.cooldown_s:.0f}s cooldown, moving on")
                        reasons.append(f"{b.name}: 429 {ex.status}")
                        break
                    if ex.code in NO_RETRY_CODES or tries >= 1:
                        self._event(res, "fallback", b.name, ex.code, f"{ex.status}; not retried")
                        reasons.append(f"{b.name}: {ex.code} {ex.status}")
                        break
                    tries += 1
                    self._event(res, "retry", b.name, ex.code, f"{ex.status}; one retry on the same model")
                    continue
                except TimeoutError:
                    self._event(res, "fallback", b.name, "timeout", "timeout, never retried")
                    reasons.append(f"{b.name}: timeout")
                    break
                except Exception as ex:   # noqa: BLE001 - network error, bad JSON ...
                    if tries >= 1:
                        self._event(res, "fallback", b.name, type(ex).__name__, "failed after one retry")
                        reasons.append(f"{b.name}: {type(ex).__name__}")
                        break
                    tries += 1
                    self._event(res, "retry", b.name, type(ex).__name__, "one retry on the same model")
                    continue
                res.ok, res.value, res.model = True, value, b.name
                if key is not None and self.cache:
                    self.cache.put(key, prompt_version, b.name, value)
                return res
        res.fallback_reason = "; ".join(reasons) or "no model configured"
        self._event(res, "template", "-", "-", res.fallback_reason)
        return res


# ---- construction from config
def make_budget(cfg: Optional[dict] = None, cache_path=None) -> Optional[LLMBudget]:
    """Budget over [primary, fallback] Gemini models from config.yaml. None when no model or no API key is configured (template mode)."""
    cfg = cfg or load_config()
    lc = cfg.get("llm") or {}
    if not lc.get("model") or os.environ.get("ECHOS_REPORT_LLM") == "off":     # run_api.sh --no-llm: never build a real client, whatever is in .env
        return None
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    from echos.reports.llm import ENV_KEY, GeminiLLM
    key = os.environ.get(ENV_KEY)
    if not key:
        return None
    rpm = lc.get("rpm") or {}
    names = [("primary", lc["model"])] + ([("fallback", lc["fallback_model"])] if lc.get("fallback_model") and lc["fallback_model"] != lc["model"] else [])
    backends = [Backend(m, GeminiLLM(key, m, lc.get("temperature", 0.2), lc.get("timeout_s", 45), 0), rpm.get(role)) for role, m in names]
    return LLMBudget(backends, Cache(cache_path or ROOT / lc.get("cache_path", "data/llm_cache.sqlite")))
