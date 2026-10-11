"""Build the weekly clinician PDF for one demo patient from data/echos.db (template narrative; no LLM, no PubMed) and print its path.
With --yes it also emails it to the CLINICIAN address only (ECHOS_CLINICIAN_EMAIL, real SMTP from .env, subject marked TEST). Without --yes nothing is sent.
Usage: PYTHONPATH=. python scripts/send_test_weekly.py [patient_id] [ISO week, e.g. 2019-W14] [--yes]"""
import sys

from echos.alerts.mailer import SMTPMailer, recipients
from echos.orchestrator import state as S
from echos.reports import weekly_report as WR


def main(args: list[str]) -> int:
    pos = [a for a in args if not a.startswith("--")]
    pid, week = (pos + [None, None])[:2]
    eng = S.get_engine()
    if pid is None:
        with eng.begin() as c:
            pid, day = c.execute(S.case_state.select().where(S.case_state.c.status == "URGENT").order_by(S.case_state.c.day)).first()[:2]
        week = week or WR.WD.iso_week(day)
    r = WR.make_weekly(eng, pid, week, dispatcher=None, send=False)
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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
