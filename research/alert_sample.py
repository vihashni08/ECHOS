"""Rebuild ONE alert payload from data/echos.db (read only) with the current template and print it. Sends nothing.
Usage: PYTHONPATH=. python research/alert_sample.py [alert_id]"""
import json
import sys

from sqlalchemy import select

from echos.alerts import payload as P
from echos.orchestrator import state as S


def main(aid=None):
    eng = S.get_engine()
    with eng.begin() as c:
        q = select(S.alerts).order_by(S.alerts.c.event_time)
        rows = c.execute(q.where(S.alerts.c.alert_id == aid) if aid else q).fetchall()
    r = rows[0]
    old = json.loads(r.payload)
    fall = {"score": old.get("fall_probability"), "evidence": {"peak_svm_g": old.get("peak_impact_g"), "post_fall_inactive": old.get("post_fall_inactivity")}}
    p = P.build_payload(eng, r.alert_id, r.type.upper(), r.patient_id, r.event_time[:10], r.event_time, r.event_id,
                        {"status": old.get("today_status", "URGENT")}, {}, fall if r.type == "fall" else None)
    print(P.render_text(p))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
