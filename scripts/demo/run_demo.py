"""Populate data/echos.db by running the full pipeline (replay -> agents -> orchestrator -> reports) on a few TIHM patients, in TEMPLATE mode with the
DryRun mailer (no Gemini, no PubMed, no email), then verify the result for the demo:
  - the mix of Normal / Monitor / Data-gap / Urgent days and the injected fall events,
  - a weekly report row and an existing PDF for every patient-week (Sunday-closed week) in the span.
The vitals model returns out-of-fold scores (ECHOS_OOF=1) so the Monitor share is not inflated by in-sample fits.
Usage: PYTHONPATH=. python3 scripts/demo/run_demo.py [n_patients] [max_days]   (systems demo, not an accuracy result; ~3 min for 4 x 28)"""
import json
import os
import sys
import warnings
from collections import Counter

warnings.filterwarnings("ignore")
from echos.offline import force_offline  # noqa: E402

force_offline()   # no Gemini, no PubMed, no real SMTP
os.environ.setdefault("ECHOS_OOF", "1")
import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

from echos.config import ROOT  # noqa: E402
from echos.orchestrator import state as S  # noqa: E402
from scripts.evaluation.kill_test import run_pipeline  # noqa: E402


def verify(db) -> dict:
    eng = S.get_engine(db)
    with eng.begin() as c:
        cases = c.execute(select(S.case_state)).fetchall()
        gt = c.execute(select(S.gt_fall)).fetchall()
        alerts = c.execute(select(S.alerts)).fetchall()
        reps = c.execute(select(S.reports.c.patient_id, S.reports.c.day, S.reports.c.kind, S.reports.c.path, S.reports.c.mode)).fetchall()
        ob = c.execute(select(S.outbox.c.channel, S.outbox.c.status)).fetchall()
    st = Counter(r.status for r in cases)
    gap = sum(bool(r.data_gap) for r in cases)
    mon_gap = sum(1 for r in cases if r.status == "MONITOR" and r.data_gap)
    no_reason = []
    for r in cases:
        rs = json.loads(r.reasons or "[]")
        trig = [x for x in rs if not x.startswith("DATA_GAP")]
        if r.status == "MONITOR" and not trig and not r.data_gap:
            no_reason.append((r.patient_id, r.day))
    # weekly: every patient-week closed by a Sunday inside the span
    days = pd.DataFrame([(r.patient_id, pd.Timestamp(r.day)) for r in cases], columns=["p", "d"])
    sundays = days[days.d.dt.weekday == 6]
    pdfs = {(r.patient_id, r.day): r.path for r in reps if r.kind == "weekly_pdf"}
    missing = [(p, d.strftime("%Y-%m-%d")) for p, d in zip(sundays.p, sundays.d) if (p, d.strftime("%Y-%m-%d")) not in pdfs
               or not os.path.exists(pdfs[(p, d.strftime("%Y-%m-%d"))])]
    return {"patient_days": len(cases), "status_days": dict(st), "data_gap_days": gap, "monitor_days_with_gap": mon_gap, "injected_falls": len(gt),
            "fall_alerts": sum(a.type == "fall" for a in alerts), "monitor_days_without_trigger": no_reason,
            "weekly_patient_weeks": len(sundays), "weekly_pdf_rows": len(pdfs), "weekly_pdfs_missing": missing,
            "mail_channels": dict(Counter(f"{c}:{s}" for c, s in ob))}


if __name__ == "__main__":
    n, d = (int(a) for a in sys.argv[1:3]) if len(sys.argv) > 2 else (4, 28)
    (ROOT / "data").mkdir(exist_ok=True)
    for f in ("echos.db", "echos.db-wal", "echos.db-shm", "cp.sqlite"):
        (ROOT / "data" / f).unlink(missing_ok=True)
    r = run_pipeline(ROOT / "data", None, n_patients=n, max_days=d, report_llm=None)  # template mode: no LLM calls
    print("cases", r["n_cases"], "alerts", len(r["alerts"]))
    v = verify(ROOT / "data" / "echos.db")
    print(json.dumps(v))
    ok = not v["weekly_pdfs_missing"] and len(v["status_days"]) >= 3 and v["data_gap_days"] > 0 and v["injected_falls"] > 0
    print("DEMO CHECKS", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)
