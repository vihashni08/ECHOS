"""LIVE check of the Gemini integration (real API calls). Refuses to run without --yes and with ECHOS_REPORT_LLM=off.
Stops at the FIRST failing check (a 404 / 429 costs one request). Default cap 4 generation requests (--max-requests N).
Startup: lists the key's models (1 listing request, see scripts/list_models.py) and refuses to run if llm.model / llm.fallback_model is
not among them (--skip-list-check to skip). Checks: primary plain request, fallback plain request, function-calling
round trip (2 requests). Prints names / status codes only (never the key or any model text). Throw-away cache file.
Usage: PYTHONPATH=. python scripts/live_llm_check.py --yes [--skip-list-check] [--max-requests 4]"""
import os
import sys
import tempfile
import time
from pathlib import Path

from echos.config import ROOT, load_config


class CapReached(RuntimeError):
    pass


class Capped:
    """Wraps a real client; refuses to send more than `cap` requests over the whole run."""
    used, cap = 0, 4

    def __init__(self, client):
        self.client, self.model = client, client.model

    def step(self, system, messages, tools=None):
        if Capped.used >= Capped.cap:
            raise CapReached("request cap reached")
        Capped.used += 1
        return self.client.step(system, messages, tools)


def reason(ex: Exception) -> tuple[str, str]:
    """(http status, one-line reason) of a client error."""
    code = getattr(ex, "code", None)
    status = str(code) if code else type(ex).__name__
    msg = " ".join(str(ex).split())
    return status, msg[:140]


def main() -> int:
    argv = sys.argv[1:]
    if "--yes" not in argv:
        print("Refusing to run: this makes REAL Gemini requests (default cap 4). Re-run with --yes.")
        return 2
    if os.environ.get("ECHOS_REPORT_LLM") == "off":
        print("ECHOS_REPORT_LLM=off: refusing to run a live check.")
        return 2
    if "--max-requests" in argv:
        Capped.cap = int(argv[argv.index("--max-requests") + 1])
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from echos.reports.llm import ENV_KEY, GeminiLLM
    if not os.environ.get(ENV_KEY):
        print("No ECHOS_LLM_API_KEY in the environment / .env: nothing to check.")
        return 1
    lc = load_config()["llm"]
    names = [m for m in (lc["model"], lc.get("fallback_model")) if m]
    print("configured models: primary=%s fallback=%s" % (lc["model"], lc.get("fallback_model") or "-"))
    key = os.environ[ENV_KEY]
    if "--skip-list-check" not in argv:
        from google import genai

        from scripts.list_models import generation_models
        try:
            avail = generation_models(genai.Client(api_key=key))
        except Exception as ex:   # noqa: BLE001
            st, why = reason(ex)
            print(f"FAIL model listing: HTTP {st}: {why}")
            return 1
        missing = [m for m in names if m not in avail]
        if missing:
            print(f"FAIL startup: not in the key's model list: {', '.join(missing)} (run scripts/list_models.py; --skip-list-check to override)")
            return 1
        print("model list check: ok")
    clients = {m: Capped(GeminiLLM(key, m, lc.get("temperature", 0.2), lc.get("timeout_s", 45), 0)) for m in names}
    primary = lc["model"]
    rows = []

    def check(name, model, fn) -> bool:
        n0, t0 = Capped.used, time.monotonic()
        try:
            ok, st, why = bool(fn()), "200", ""
            if not ok:
                st, why = "-", "unexpected answer"
        except Exception as ex:   # noqa: BLE001
            ok, (st, why) = False, reason(ex)
        rows.append((name, model, Capped.used - n0, st, f"{time.monotonic() - t0:.1f}", "ok" if ok else f"FAIL: {why}"))
        return ok

    ping = [{"role": "user", "text": 'Reply with the JSON {"ok": true} and nothing else.'}]
    plain = lambda c: '"ok"' in c.step("Reply with JSON only.", ping, None).text.replace("'", '"')   # noqa: E731
    tool = [{"name": "get_number", "description": "Returns the lucky number.", "parameters": {"type": "object", "properties": {}}}]

    def tool_roundtrip():
        c = clients[primary]
        msgs = [{"role": "user", "text": "Call get_number, then reply with JSON {\"n\": <the number>}."}]
        r = c.step("Use the tool.", msgs, tool)
        if not r.calls:
            return False
        msgs += [{"role": "model", "calls": r.calls, "raw": r.raw}, {"role": "tool", "results": [{"name": r.calls[0]["name"], "response": {"number": 7}}]}]
        return "7" in c.step("Use the tool.", msgs, None).text

    steps = [("primary plain request", primary, lambda: plain(clients[primary]))]
    fb = lc.get("fallback_model")
    if fb and fb != primary:
        steps.append(("fallback plain request", fb, lambda: plain(clients[fb])))
    steps.append(("function calling (2 requests)", primary, tool_roundtrip))
    for s in steps:
        if not check(*s):
            break
    hdr = ("check", "model", "requests", "http", "seconds", "result")
    w = [max(len(str(x)) for x in col) for col in zip(hdr, *rows)]
    print("  ".join(h.ljust(n) for h, n in zip(hdr, w)))
    for r_ in rows:
        print("  ".join(str(x).ljust(n) for x, n in zip(r_, w)))
    stopped = any(r_[-1].startswith("FAIL") for r_ in rows)
    print(f"total real requests: {Capped.used} (cap {Capped.cap})" + ("; stopped at first failure" if stopped else ""))
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
