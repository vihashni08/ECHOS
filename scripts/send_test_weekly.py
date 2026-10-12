"""Build the weekly clinician PDF for one demo patient from data/echos.db (template narrative, no LLM; retrieval attached: local index, then live PubMed for Monitor/Urgent weeks) and print its path.
With --yes it also emails it to the CLINICIAN address only (ECHOS_CLINICIAN_EMAIL, real SMTP from .env, subject marked TEST). Without --yes nothing is sent.
Default week: the patient's most recent week in which the vitals baselines are established; override with --week 2019-W14 (or the second positional argument).
Usage: PYTHONPATH=. python scripts/send_test_weekly.py [patient_id] [--week 2019-W14] [--yes]   (--all: every patient of the database, nothing sent)"""
import sys

from echos.alerts.mailer import SMTPMailer, recipients
from echos.orchestrator import state as S
from echos.reports import weekly_report as WR


def main(args: list[str]) -> int:
    from echos.offline import force_offline
    if "--yes" not in args:
        force_offline()                     # build only: never any network
    week_opt = args[args.index("--week") + 1] if "--week" in args else None
    pos = [a for i, a in enumerate(args) if not a.startswith("--") and not (i and args[i - 1] == "--week")]
    pid, week = (pos + [None, None])[:2]
    week = week_opt or week
    eng = S.get_engine()
    if "--all" in args:
        from sqlalchemy import select
        with eng.begin() as c:
            pids = sorted({r[0] for r in c.execute(select(S.case_state.c.patient_id))})
        for p in pids:
            w = week or WR.latest_established_week(eng, p)
            r = WR.make_weekly(eng, p, w, dispatcher=None, retriever=_retriever(eng), send=False)
            print(f"{p} {w}: {r['pdf']['path']} ({r['pdf']['pages']} pages)")
        return 0
    if pid is None:
        with eng.begin() as c:
            pid, day = c.execute(S.case_state.select().where(S.case_state.c.status == "URGENT").order_by(S.case_state.c.day)).first()[:2]
    week = week or WR.latest_established_week(eng, pid)
    retriever = _retriever(eng)      # same retriever as the orchestrator: local index first, then PubMed (only if the week has a Monitor/Urgent day)
    r = WR.make_weekly(eng, pid, week, dispatcher=None, retriever=retriever, send=False)
    print(f"PDF: {r['pdf']['path']} ({r['pdf']['pages']} pages, {r['pdf']['bytes'] // 1024} KB)")
    if "--yes" not in args:
        print("Not sent. Re-run with --yes to email it to the clinician address only.")
        return 0
    to = recipients()["clinician"]
    if not to:
        print("ECHOS_CLINICIAN_EMAIL is not set: nothing sent.")
        return 1
    subject, text, html = WR.email_parts(r["data"])
    try:
        SMTPMailer().send(to, "[ECHOS TEST] " + subject, text, html,
                          attachments=[{"filename": r["pdf"]["path"].split("/")[-1], "data": open(r["pdf"]["path"], "rb").read(), "mime": "application/pdf"}])
        print("clinician: SENT")
    except Exception as ex:  # noqa: BLE001
        print(f"clinician: FAILED ({type(ex).__name__})")
        return 1
    return 0


def _retriever(eng):
    from echos.reports.agent import ReportAgent
    return ReportAgent(eng, None).retriever


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
