"""Alert latency under simulated load: event (just before the payload is built) -> queued (outbox row written) -> sent
(outbox sent_time), with FakeMailer only (never real email), background CPU-busy threads and a slow mailer.
Usage: PYTHONPATH=. python scripts/alert_latency.py  -> reports/metrics/alert_latency.json"""
import json
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
from sqlalchemy import select

from echos.alerts import payload as P
from echos.alerts.dispatcher import AlertDispatcher
from echos.alerts.mailer import FakeMailer
from echos.config import ROOT
from echos.orchestrator import state as S

TO = {"caregiver": "care@example.test", "clinician": "doc@example.test"}


def measure(n_alerts: int = 40, load_threads: int = 4, mailer_delay_s: float = 0.02, rate_per_s: float = 20.0, path=None) -> dict:
    eng = S.get_engine(path or Path(tempfile.mkdtemp()) / "lat.db")
    mailer = FakeMailer(delay_s=mailer_delay_s)
    d = AlertDispatcher(eng, mailer, to=TO)
    stop = threading.Event()

    def burn():
        x = 0
        while not stop.is_set():
            x = (x * 31 + 7) % 1000003
    ts = [threading.Thread(target=burn, daemon=True) for _ in range(load_threads)]
    [t.start() for t in ts]
    t0s = {}
    for i in range(n_alerts):
        aid = f"lat|2019-05-01|fall|{i}"
        t0s[aid] = time.time()                                      # the event is detected
        p = P.AlertPayload(alert_id=aid, alert_type="FALL", patient_id="lat", event_time="2019-05-01T10:00:00", event_id=i,
                           fall_probability=0.97, peak_impact_g=5.2, post_fall_inactivity=True)
        d.dispatch(p)
        time.sleep(1.0 / rate_per_s)
    ok = d.flush(60)
    stop.set()
    with eng.begin() as c:
        rows = c.execute(select(S.outbox.c.alert_id, S.outbox.c.queued_time, S.outbox.c.sent_time, S.outbox.c.status)).fetchall()
    q = np.array([r.queued_time - t0s[r.alert_id] for r in rows])
    s = np.array([r.sent_time - t0s[r.alert_id] for r in rows if r.sent_time])
    d.close()
    f = lambda a: {"median_s": float(np.median(a)), "p95_s": float(np.percentile(a, 95)), "max_s": float(a.max())}  # noqa: E731
    return {"alerts": n_alerts, "outbox_rows": len(rows), "all_sent": bool(ok and all(r.status == "SENT" for r in rows)),
            "load_threads": load_threads, "mailer_delay_s": mailer_delay_s, "rate_alerts_per_s": rate_per_s,
            "event_to_queued": f(q), "event_to_sent": f(s)}


if __name__ == "__main__":
    r = {"no_load": measure(load_threads=0), "load_4_busy_threads": measure(load_threads=4)}
    (ROOT / "reports/metrics/alert_latency.json").write_text(json.dumps(r, indent=2))
    print(json.dumps(r))
