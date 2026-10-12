"""LIVE check of the report path with the REAL Gemini (through the budget manager: limiter, cooldown, fallback, validator) and the REAL PubMed.
Generates 2 daily reports (one MONITOR, one URGENT with a fall) and 1 weekly 'Key findings' narrative from the demo database (copied to a temp
directory: the database is never modified). Both go through the same budget manager (cache -> primary -> fallback -> template); the weekly narrative is
written by the real model, not the template. Refuses to run without --yes, and with ECHOS_REPORT_LLM=off / ECHOS_PUBMED=off.
Hard caps: 6 Gemini requests per item, 12 over the whole run, 6 PubMed requests (a request over a cap is not sent; reported as 'cap reached (script)',
separately from Google errors). Stops at the FIRST 404 or the FIRST failure of the primary model: no further request is sent and the remaining items are
marked not run. The response cache is a temp file, so every item really calls the model.
Prints one table: item, model used or template fallback with reason, tool calls made, validator result on the first attempt, sources cited, seconds,
requests used. On a validator failure it prints which rule failed (rule name + a one-line detail such as the offending number or item index, never the
text) and saves the raw model output and the validator report to reports/live_check/<item>.json (git-ignored). Report text only with --show.
No e-mail is ever sent (no dispatcher is built; ECHOS_DRY_RUN=1 is forced).
Usage: PYTHONPATH=. python scripts/live_report_check.py --yes [--only monitor|urgent|weekly] [--show] [--db data/echos.db]"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_GEMINI, MAX_PUBMED, MAX_PER_ITEM = 12, 6, 6
ITEMS = ("monitor", "urgent", "weekly")
OUT_DIR = ROOT / "reports" / "live_check"


class CapReached(RuntimeError):
    pass


class Run:
    """Shared counters / stop flag for one live run. Requests are counted per item (`item` is set by run_check) and overall."""
    def __init__(self, max_gemini=MAX_GEMINI, max_pubmed=MAX_PUBMED, max_per_item=MAX_PER_ITEM):
        self.max_gemini, self.max_pubmed, self.max_per_item = max_gemini, max_pubmed, max_per_item
        self.gemini = self.pubmed = 0
        self.stop_reason = None
        self.item = None
        self.per_item: dict = {}       # item -> requests sent
        self.cap_hit: dict = {}        # item -> which script cap blocked a request
        self.google_err: dict = {}     # item -> "429 RESOURCE_EXHAUSTED" etc. (an error returned by Google, not by this script)
        self.raw: dict = {}            # item -> [raw model text per request]

    def start_item(self, name: str) -> None:
        self.item = name
        self.per_item.setdefault(name, 0)
        self.raw.setdefault(name, [])


class CappedClient:
    """Wraps a model client: counts requests, never sends one over the cap or after a stop; a 404 or any primary-model failure stops the run."""
    def __init__(self, client, run: Run, primary: bool):
        self.client, self.run, self.primary, self.model = client, run, primary, client.model

    def step(self, system, messages, tools=None):
        r, it = self.run, self.run.item
        if r.stop_reason:
            raise CapReached(f"stopped: {r.stop_reason}")
        if it is not None and r.per_item.get(it, 0) >= r.max_per_item:
            r.cap_hit.setdefault(it, f"per-item cap {r.max_per_item}")
            raise CapReached(f"per-item cap {r.max_per_item} reached")
        if r.gemini >= r.max_gemini:
            if it is not None:
                r.cap_hit.setdefault(it, f"global cap {r.max_gemini}")
            raise CapReached(f"request cap {r.max_gemini} reached")
        r.gemini += 1
        if it is not None:
            r.per_item[it] = r.per_item.get(it, 0) + 1
        try:
            out = self.client.step(system, messages, tools)
        except Exception as ex:   # noqa: BLE001
            code = getattr(ex, "code", None)
            if it is not None:
                r.google_err[it] = f"{code or type(ex).__name__} {getattr(ex, 'status', '')}".strip()
            if code == 404 or self.primary:
                r.stop_reason = f"{'HTTP 404' if code == 404 else 'primary model failed'} on {self.model} ({type(ex).__name__}{' ' + str(code) if code else ''})"
            raise
        if it is not None:
            r.raw.setdefault(it, []).append(getattr(out, "text", "") or "")
        return out


def capped_pubmed_get(run: Run, real_get):
    def get(url, params, timeout):
        if run.stop_reason or run.pubmed >= run.max_pubmed:
            raise CapReached("PubMed request cap reached" if not run.stop_reason else f"stopped: {run.stop_reason}")
        run.pubmed += 1
        return real_get(url, params, timeout)
    return get


def pick_items(db: Path) -> dict:
    """-> {'monitor': (patient, day, facts), 'urgent': (patient, day, facts), 'week': (patient, 'YYYY-Www')} from the stored database (read-only)."""
    from echos.reports import weekly_data as WD
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("select c.patient_id, c.day, c.status, c.data_gap, f.facts from case_state c join daily_facts f on f.patient_id=c.patient_id and f.day=c.day "
                       "order by c.day desc, c.patient_id").fetchall()
    out = {}
    for pid, day, status, gap, facts in rows:
        f = json.loads(facts)
        real = [x for x in f["flags"] if x["type"] != "data_gap"]
        if status == "MONITOR" and not gap and real and "monitor" not in out:
            out["monitor"] = (pid, day, f)
        if status == "URGENT" and f["falls"] and "urgent" not in out:
            out["urgent"] = (pid, day, f)
    if "monitor" in out:
        out["week"] = (out["monitor"][0], WD.iso_week(out["monitor"][1]))
    return out


def outcome_reason(run: Run, item: str, prov: dict) -> str:
    """Why the template was used. A script cap, a Google error and a validator rejection are three different things and are labelled as such."""
    if item in run.cap_hit:
        return f"cap reached (script): {run.cap_hit[item]}"
    if item in run.google_err:
        return f"Google error {run.google_err[item]}"
    fr = prov.get("fallback_reason") or "no reason recorded"
    codes = prov.get("validator_codes") or (prov.get("validator_failure") or {}).get("codes")
    return f"validator failed: {', '.join(codes)}" if codes and "validation_failed" in fr else fr


def report_failures(run: Run, item: str, label: str, first: list, prov: dict, out_dir: Path) -> None:
    """Print the failed rules of the first attempt (rule + one-line detail, no text) and save raw output + validator report for this item."""
    failed = [v for v in first if not v["ok"]]
    if not failed:
        return
    for n, v in enumerate(first, 1):
        for f in v.get("findings", []) or [{"rule": "other", "detail": e} for e in v.get("errors", [])]:
            if not v["ok"]:
                print(f"  [{label}] check {n}: rule={f['rule']}: {f['detail'][:140]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{item}.json").write_text(json.dumps({"item": item, "requests": run.per_item.get(item, 0), "raw_model_output": run.raw.get(item, []),
                                                        "validator_by_check": first, "final_provenance": {k: prov.get(k) for k in ("model", "mode", "fallback_reason", "validator", "validator_failure", "requests")}},
                                                       indent=1, default=str))
    print(f"  [{label}] raw output + validator report saved to {out_dir.relative_to(ROOT) if out_dir.is_relative_to(ROOT) else out_dir}/{item}.json")


def run_check(db: Path, run: Run, budget, pubmed, show: bool = False, only: str | None = None, out_dir: Path | None = None) -> list[tuple]:
    """The three generations. `budget` is an LLMBudget whose clients are CappedClient; `pubmed` a PubMedClient with the capped transport."""
    from echos.orchestrator import state as S
    from echos.reports import evidence as EV
    from echos.reports import weekly_data as WD
    from echos.reports import weekly_narrative as WN
    from echos.reports.agent import ReportAgent
    tmp = Path(tempfile.mkdtemp(prefix="echos_live_"))
    try:
        shutil.copy(db, tmp / "echos.db")
        eng = S.get_engine(tmp / "echos.db")
        picks = pick_items(db)
        agent = ReportAgent(eng, None, root=tmp, budget=budget, pubmed=pubmed)
        first: list = []           # validator result (dict) of each _check call; first[0] = first attempt
        out_dir = Path(out_dir) if out_dir else OUT_DIR
        orig_check, orig_wcheck = ReportAgent._check, WN._check

        def rec(orig):
            def f(*a, **k):
                v = orig(*a, **k)
                first.append(v)
                return v
            return f
        ReportAgent._check = staticmethod(rec(orig_check))
        WN._check = rec(orig_wcheck)
        rows, shown = [], []
        try:
            for name, label in (("monitor", "daily MONITOR day"), ("urgent", "daily URGENT day (fall)"), ("week", "weekly Key findings")):
                if only and name != ("week" if only == "weekly" else only):
                    continue
                if name not in picks:
                    rows.append((label, "not run: no such day in the database", "-", "-", "-", "-", "0"))
                    continue
                if run.stop_reason:
                    rows.append((label, f"not run: {run.stop_reason}", "-", "-", "-", "-", "0"))
                    continue
                first.clear()
                run.start_item("weekly" if name == "week" else name)
                t0 = time.monotonic()
                prov = {}
                try:
                    if name == "week":
                        pid, wk = picks["week"]
                        d = WD.build_weekly_data(eng, pid, wk)
                        status = "URGENT" if d["status_counts"]["URGENT"] else "MONITOR"
                        _, sources, _ = EV.gather_findings(d, agent.retriever, status)
                        res = WN.make_narrative(d, sources, budget=budget, use_llm=True)
                        prov = res["provenance"]
                        cited = sorted({c for g in res["items"].get("guidance", []) for c in g.get("cites", [])})
                        tools, text = "-", res["text"]
                    else:
                        pid, day, facts = picks[name]
                        res = agent.daily(facts, save=False, defer_llm=False, use_llm=True)
                        prov = res["provenance"]
                        cited = prov["sources_cited"]
                        tools = ", ".join(t["name"] for t in prov["tools"]) or ("0" if res["mode"] == "llm" else "-")
                        text = res["text"]
                    item = run.item
                    model = prov["model"] if res["mode"] == "llm" else f"template ({outcome_reason(run, item, prov)})"
                    ok1 = ("pass" if first[0]["ok"] else "FAIL (retry / template)") if first else "n/a (no model output)"
                    rows.append((label, model + (" [cache]" if prov.get("cache_hit") else ""), tools, ok1, ", ".join(cited) or "-", f"{time.monotonic() - t0:.1f}",
                                 str(run.per_item.get(item, 0))))
                    shown.append((label, text))
                    report_failures(run, item, label, first, prov, out_dir)
                except Exception as ex:   # noqa: BLE001 - one item failing must not hide the table
                    rows.append((label, f"error: {type(ex).__name__}: {str(ex)[:80]}", "-", "-", "-", f"{time.monotonic() - t0:.1f}", str(run.per_item.get(run.item, 0))))
        finally:
            ReportAgent._check, WN._check = staticmethod(orig_check), orig_wcheck
        if show:
            for label, text in shown:
                print(f"\n===== {label} =====\n{text}")
        return rows
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def print_table(rows: list[tuple], run: Run) -> None:
    hdr = ("item", "model / fallback", "tool calls", "validator, 1st attempt", "sources cited", "seconds", "requests")
    w = [max(len(str(x)) for x in col) for col in zip(hdr, *rows)]
    print("  ".join(h.ljust(n) for h, n in zip(hdr, w)))
    for r_ in rows:
        print("  ".join(str(x).ljust(n) for x, n in zip(r_, w)))
    print(f"real Gemini requests: {run.gemini} (cap {run.max_gemini}, per item {run.max_per_item}); real PubMed requests: {run.pubmed} (cap {run.max_pubmed})"
          + (f"; STOPPED: {run.stop_reason}" if run.stop_reason else ""))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--yes" not in argv:
        print(f"Refusing to run: this makes REAL Gemini (<= {MAX_GEMINI}, <= {MAX_PER_ITEM} per item) and PubMed (<= {MAX_PUBMED}) requests. Re-run with --yes.")
        return 2
    if os.environ.get("ECHOS_REPORT_LLM") == "off" or os.environ.get("ECHOS_PUBMED") == "off":
        print("ECHOS_REPORT_LLM=off / ECHOS_PUBMED=off: refusing to run a live check.")
        return 2
    only = argv[argv.index("--only") + 1] if "--only" in argv and argv.index("--only") + 1 < len(argv) else None
    if "--only" in argv and only not in ITEMS:
        print(f"--only takes one of: {', '.join(ITEMS)}")
        return 2
    os.environ["ECHOS_DRY_RUN"] = "1"          # no e-mail, whatever happens
    db = Path(argv[argv.index("--db") + 1]) if "--db" in argv else ROOT / "data/echos.db"
    if not db.exists():
        print(f"No demo database at {db} (build it with scripts/run_demo.py).")
        return 1
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from echos.knowledge import pubmed as PM
    from echos.reports import llm_budget as LB
    from echos.reports.llm import ENV_KEY
    if not os.environ.get(ENV_KEY):
        print("No ECHOS_LLM_API_KEY in the environment / .env: nothing to check.")
        return 1
    run = Run()
    tmp_cache = Path(tempfile.mkdtemp(prefix="echos_live_cache_")) / "cache.sqlite"
    budget = LB.make_budget(cache_path=tmp_cache)
    if budget is None:
        print("No LLM model configured (llm.model in config.yaml).")
        return 1
    for i, b in enumerate(budget.backends):
        b.client = CappedClient(b.client, run, primary=(i == 0))
    pubmed = PM.PubMedClient(http_get=capped_pubmed_get(run, PM._http_get), cache_path=tmp_cache.with_name("pubmed.sqlite"))
    rows = run_check(db, run, budget, pubmed, show="--show" in argv, only=only)
    print_table(rows, run)
    shutil.rmtree(tmp_cache.parent, ignore_errors=True)
    bad = bool(run.stop_reason) or any(r[1].startswith(("error", "template")) for r in rows)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
