"""Generate 10 sample reports (8 daily + 2 weekly) from data/echos.db with the real LLM + RAG, into reports/samples/ (git-ignored).
Works on a COPY of the demo DB. Stops at the first HTTP 429 (no retry, not even the client's single one). Prints a table, no report text.
Usage: PYTHONPATH=. python3 scripts/realservices/make_samples.py"""
import json
import re
import shutil
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
from echos.config import ROOT  # noqa: E402
from echos.orchestrator import state as S  # noqa: E402
from echos.reports.daily import agent as A  # noqa: E402
from echos.reports.llm.llm import make_llm  # noqa: E402

DAILY = [("c55f8", "2019-04-02"), ("c55f8", "2019-04-06"), ("ec812", "2019-04-02"), ("c55f8", "2019-04-04"),
         ("ec812", "2019-04-19"), ("c5785", "2019-05-07"), ("6b29b", "2019-05-04"), ("c5785", "2019-05-02")]
WEEKLY = [("c55f8", "2019-04-17"), ("c5785", "2019-05-18")]

if "--yes" not in sys.argv:
    sys.exit("Refusing to run: this makes REAL Gemini requests. Re-run with --yes.")
llm = make_llm()
if llm is None:
    sys.exit("no LLM configured")
llm.generate = llm._once  # no client-side 429 retry
tmp = Path(tempfile.mkdtemp())
shutil.copy(ROOT / "data/echos.db", tmp / "t.db")
eng = S.get_engine(tmp / "t.db")
ag = A.ReportAgent(eng, llm, root=tmp)
cap = {}
orig = A._write


def spy(*a, **k):
    r = orig(*a, **k)
    cap["items"] = r[0]
    return r


A._write = spy
out_dir = ROOT / "reports/samples"
shutil.rmtree(out_dir, ignore_errors=True)
out_dir.mkdir(parents=True)
rows = []


def cites(items):
    return sorted({c for v in items.values() if isinstance(v, list) for it in v if isinstance(it, dict) for c in it.get("cites", [])})


def run(kind, pid, day):
    if kind == "daily":
        with eng.begin() as c:
            facts = json.loads(c.execute(S.daily_facts.select().where((S.daily_facts.c.patient_id == pid) & (S.daily_facts.c.day == day))).first().facts)
            st = c.execute(S.case_state.select().where((S.case_state.c.patient_id == pid) & (S.case_state.c.day == day))).first().status
        o = ag.daily(facts, save=False)
    else:
        st = "weekly"
        o = ag.weekly(pid, day)
    v = o["validation"]
    shutil.copy(o["path"], out_dir / f"{pid}_{day}{o['path'].suffix}")
    reason = v.get("fallback_reason")
    rows.append((f"{kind} {pid} {day}", st, "LLM" if o["mode"] == "llm" else f"fallback: {reason}",
                 "ok" if v["ok"] else "FAIL", ",".join(cites(cap["items"])) or "-", "; ".join(v.get("notes", []) + v.get("errors", []))[:90] or "-"))
    return reason


stopped = None
for k, lst in (("daily", DAILY), ("weekly", WEEKLY)):
    for pid, day in lst:
        r = run(k, pid, day)
        if r and re.search(r"\b429\b", r):
            stopped = f"{k} {pid} {day}"
            break
    if stopped:
        break
print("| report | status | mode | validator | citations | failed checks |\n|---|---|---|---|---|---|")
for r in rows:
    print("| " + " | ".join(r) + " |")
if stopped:
    print(f"STOPPED on 429 at {stopped}")
