"""Send ONE clearly labelled TEST alert email to each recipient (ECHOS_CAREGIVER_EMAIL, ECHOS_CLINICIAN_EMAIL) using the real
SMTP settings from .env (ECHOS_SMTP_HOST/PORT/USER/PASSWORD). Needs --yes; without it nothing is sent. Prints status only,
never the password or the full addresses. Independent of config alerts.send_real (this is an explicit manual action).
Usage: PYTHONPATH=. python scripts/send_test_email.py --yes"""
import sys

from echos.alerts import payload as P
from echos.alerts.mailer import SMTPMailer, recipients


def mask(a: str) -> str:
    return "(not set)" if not a else a[:2] + "***@" + a.split("@")[-1] if "@" in a else "***"


def main(yes: bool) -> int:
    to, m = recipients(), SMTPMailer()
    print(f"SMTP configured: {m.configured()} (host {m.host or '(not set)'}:{m.port}); recipients: " + ", ".join(f"{r}={mask(a)}" for r, a in to.items()))
    if not yes:
        print("Dry check only. Re-run with --yes to send one TEST email to each recipient.")
        return 0
    p = P.AlertPayload(alert_id="TEST", alert_type="FALL", patient_id="TEST-PATIENT", age_band="(70, 80]", event_time="2019-01-01T12:00:00",
                       fall_probability=0.99, peak_impact_g=5.0, post_fall_inactivity=True, today_status="URGENT", is_test=True,
                       data_quality_note="this is a test, no real event")
    rc = 0
    for role, addr in to.items():
        if not addr:
            print(f"{role}: FAILED (no address in env)")
            rc = 1
            continue
        try:
            m.send(addr, P.subject(p), P.render_text(p), P.render_html(p))
            print(f"{role}: SENT")
        except Exception as ex:  # noqa: BLE001
            print(f"{role}: FAILED ({type(ex).__name__})")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main("--yes" in sys.argv))
