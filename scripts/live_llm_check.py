"""LIVE check of the Gemini integration (real API calls, at most 6 requests in total). Refuses to run without --yes.
Checks: primary model answers, fallback model answers, function calling round trip, budget cache hit (no request).
Prints only a table (never the key or any model text). Uses a throw-away cache file.
Usage: PYTHONPATH=. python scripts/live_llm_check.py --yes"""
import os
import sys
import tempfile
import time
from pathlib import Path

from echos.config import ROOT, load_config

MAX_REQUESTS = 6


class Capped:
    """Wraps a real client; refuses to send more than MAX_REQUESTS requests over the whole run."""
    used = 0

    def __init__(self, client):
        self.client, self.model = client, client.model

    def step(self, system, messages, tools=None):
        if Capped.used >= MAX_REQUESTS:
            raise RuntimeError("request cap reached")
        Capped.used += 1
        return self.client.step(system, messages, tools)


def main() -> int:
    if "--yes" not in sys.argv:
        print("Refusing to run: this makes up to 6 REAL Gemini requests. Re-run with --yes.")
        return 2
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from echos.reports.llm import ENV_KEY, GeminiLLM
    if not os.environ.get(ENV_KEY):
        print("No ECHOS_LLM_API_KEY in the environment / .env: nothing to check.")
        return 1
    from echos.reports.llm_budget import Backend, Cache, LLMBudget, make_key
    lc = load_config()["llm"]
    key = os.environ[ENV_KEY]
    clients = {m: Capped(GeminiLLM(key, m, lc.get("temperature", 0.2), lc.get("timeout_s", 45), 0)) for m in (lc["model"], lc.get("fallback_model")) if m}
    rows = []

    def row(check, model, fn):
        n0, t0 = Capped.used, time.monotonic()
        try:
            res = fn()
            status = "200"
        except Exception as ex:   # noqa: BLE001
            res, status = False, str(getattr(ex, "code", type(ex).__name__))
        rows.append((check, model, Capped.used - n0, status, f"{time.monotonic() - t0:.1f}", "ok" if res else "FAIL"))

    ping = [{"role": "user", "text": 'Reply with the JSON {"ok": true} and nothing else.'}]
    for m, c in clients.items():
        row("plain request", m, lambda c=c: '"ok"' in c.step("Reply with JSON only.", ping, None).text.replace("'", '"'))
    primary = lc["model"]
    tool = [{"name": "get_number", "description": "Returns the lucky number.", "parameters": {"type": "object", "properties": {}}}]

    def tool_roundtrip():
        c = clients[primary]
        msgs = [{"role": "user", "text": "Call get_number, then reply with JSON {\"n\": <the number>}."}]
        r = c.step("Use the tool.", msgs, tool)
        if not r.calls:
            return False
        msgs += [{"role": "model", "calls": r.calls, "raw": r.raw}, {"role": "tool", "results": [{"name": r.calls[0]["name"], "response": {"number": 7}}]}]
        return "7" in c.step("Use the tool.", msgs, None).text
    row("function calling (2 requests)", primary, tool_roundtrip)

    budget = LLMBudget([Backend(m, c, (lc.get("rpm") or {}).get(role)) for (m, c), role in zip(clients.items(), ("primary", "fallback"))],
                       Cache(Path(tempfile.mkdtemp()) / "live_cache.sqlite"))
    k = make_key("live-check", {"x": 1})
    attempt = lambda ctx: {"ok": '"ok"' in ctx.step("Reply with JSON only.", ping, None).text.replace("'", '"')}   # noqa: E731
    row("budget: first call", primary, lambda: budget.run(attempt, key=k, prompt_version="live-check").ok)
    row("budget: cache hit (0 requests)", "-", lambda: budget.run(attempt, key=k, prompt_version="live-check").cache_hit)
    w = [max(len(str(x)) for x in col) for col in zip(("check", "model", "requests", "http", "seconds", "result"), *rows)]
    print("  ".join(h.ljust(n) for h, n in zip(("check", "model", "requests", "http", "seconds", "result"), w)))
    for r_ in rows:
        print("  ".join(str(x).ljust(n) for x, n in zip(r_, w)))
    print(f"total real requests: {Capped.used} (cap {MAX_REQUESTS})")
    return 0 if all(r_[-1] == "ok" for r_ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
