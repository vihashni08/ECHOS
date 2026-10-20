"""Rebuild the daily reports of an existing database from its stored daily facts with the CURRENT report code (new layout, plan, references).
Writes the .md and .json next to each other under the database's files directory and updates the `reports` rows; nothing else in the database changes.
  PYTHONPATH=. python scripts/demo/regenerate_daily_reports.py --db data/echos_ui_dev.db            template reports, local guideline index only (offline)
  PYTHONPATH=. python scripts/demo/regenerate_daily_reports.py --db data/echos_ui_dev.db --llm --yes --limit 10
        REAL Gemini + PubMed for the first --limit non-NORMAL days (<= 1-2 Gemini requests each; refuses without --yes).
Never run against data/echos.db for UI work: use the dev copy (scripts/make_ui_dev_copy.sh)."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/echos_ui_dev.db")
    ap.add_argument("--root", help="files directory (default: <db without .db>_files)")
    ap.add_argument("--llm", action="store_true", help="use the real Gemini + PubMed for non-NORMAL days (needs --yes)")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--limit", type=int, default=10, help="max non-NORMAL days sent to the LLM")
    a = ap.parse_args(argv)
    if a.llm and not a.yes:
        print("Refusing: --llm makes real Gemini and PubMed requests. Re-run with --yes.")
        return 2
    if not a.llm:
        os.environ.update(ECHOS_REPORT_LLM="off", ECHOS_PUBMED="off")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import json

    from sqlalchemy import select

    from echos.orchestrator import state as S
    from echos.reports.daily.agent import ReportAgent
    from echos.reports.llm.llm_budget import make_budget
    db = Path(a.db) if Path(a.db).is_absolute() else ROOT / a.db
    root = Path(a.root) if a.root else db.with_name(db.stem + "_files")
    eng = S.get_engine(db)
    budget = make_budget() if a.llm else None
    agent = ReportAgent(eng, None, root=root, budget=budget)
    with eng.begin() as c:
        rows = c.execute(select(S.daily_facts.c.facts).order_by(S.daily_facts.c.day, S.daily_facts.c.patient_id)).fetchall()
    n = n_llm = 0
    for (raw,) in rows:
        f = json.loads(raw)
        use_llm = a.llm and f["status"] != "NORMAL" and n_llm < a.limit
        n_llm += int(use_llm)
        agent.daily(f, save=False, use_llm=use_llm)
        n += 1
    print(f"rebuilt {n} daily reports under {root} ({n_llm} through the LLM)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
