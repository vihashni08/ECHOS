"""Dispatcher: one dispatch -> caregiver AND clinician. Rows are queued in the outbox (PENDING) and sent by ONE background
worker thread, so triage and the daily report never wait on a mail server. Per recipient: PENDING -> SENT | FAILED, up to
`max_attempts` attempts with backoff (retries are scheduled, not slept: a failing recipient never delays other alerts).
A failure is logged and stays visible in the outbox / alerts tables; it never looks delivered."""
from __future__ import annotations

import heapq
import itertools
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select

from echos.alerts import payload as P
from echos.alerts.mailer import DryRunMailer, Mailer, recipients as env_recipients
from echos.config import load_config
from echos.orchestrator import state as S

log = logging.getLogger("echos.alerts")


def _redact(msg: str) -> str:
    for k in ("ECHOS_SMTP_PASSWORD", "ECHOS_SMTP_USER"):
        v = os.environ.get(k)
        if v:
            msg = msg.replace(v, "***")
    return msg[:200]


class AlertDispatcher:
    def __init__(self, eng, mailer: Optional[Mailer] = None, to: Optional[dict] = None, max_attempts: Optional[int] = None,
                 backoff_s: Optional[list] = None, cooldown_sim_h: Optional[float] = None):
        a = load_config().get("alerts", {})
        self.eng, self.mailer = eng, mailer or DryRunMailer()
        self.to = to if to is not None else env_recipients()
        self.max_attempts = max_attempts or a.get("max_attempts", 3)
        self.backoff = list(backoff_s if backoff_s is not None else a.get("backoff_s", [1, 2, 4]))
        self.cooldown_h = a.get("critical_cooldown_sim_h", 12) if cooldown_sim_h is None else cooldown_sim_h
        self._heap, self._seq, self._cv = [], itertools.count(), threading.Condition()
        self._inflight, self._stop, self._threads = 0, False, []
        self.n_workers = a.get("workers", 3)   # a slow / hung mail server delays at most one worker, not every alert

    # ---- producer side (fast: two small SQLite writes, then returns)
    def dispatch(self, p: P.AlertPayload) -> bool:
        """Queue one alert for both recipients. False if it is a duplicate (same alert_id) or inside the critical-vitals cooldown."""
        now = time.time()
        if p.alert_type == "CRITICAL_VITALS" and self._in_cooldown(p):
            log.info("alert suppressed (cooldown): %s", p.alert_id)
            return False
        subj, text, html = P.subject(p), P.render_text(p), P.render_html(p)
        rows = []
        status = "PENDING"
        with self.eng.begin() as c:
            if not S.insert_ignore(c, S.alerts, alert_id=p.alert_id, patient_id=p.patient_id, type=p.alert_type.lower(), time=now,
                                   payload=S.j(p.model_dump(mode="json")), status="PENDING", event_id=p.event_id, event_time=p.event_time):
                return False   # dedup: this event already has its alert
            for role in ("caregiver", "clinician"):
                addr = self.to.get(role, "")
                st, err = ("PENDING", None) if addr else ("FAILED", "no recipient address configured")
                r = c.execute(S.outbox.insert().values(alert_id=p.alert_id, recipient_role=role, address=addr, channel=self.mailer.channel,
                                                       subject=subj, body=text, body_html=html, status=st, attempts=0, error=err,
                                                       event_time=p.event_time, queued_time=time.time()))
                rows.append((r.inserted_primary_key[0], st, {"address": addr, "subject": subj, "body": text, "body_html": html,
                                                            "alert_id": p.alert_id, "role": role, "attempts": 0}))
            if all(st == "FAILED" for _, st, _ in rows):
                c.execute(S.alerts.update().where(S.alerts.c.alert_id == p.alert_id).values(status="FAILED"))
        for oid, st, job in rows:
            if st == "PENDING":
                self._push(oid, 0.0, job)
            else:
                log.error("alert %s: recipient has no address", p.alert_id)
        return True

    def _in_cooldown(self, p: P.AlertPayload) -> bool:
        try:
            t = datetime.fromisoformat(p.event_time)
        except ValueError:
            return False
        with self.eng.begin() as c:
            rows = c.execute(select(S.alerts.c.alert_id, S.alerts.c.event_time).where(
                (S.alerts.c.patient_id == p.patient_id) & (S.alerts.c.type == "critical_vitals"))).fetchall()
        for aid, et in rows:
            if aid != p.alert_id and et:
                try:
                    if abs((t - datetime.fromisoformat(et)).total_seconds()) < self.cooldown_h * 3600:
                        return True
                except ValueError:
                    continue
        return False

    # ---- worker
    def _push(self, outbox_id: int, delay: float, job: Optional[dict] = None):
        with self._cv:
            heapq.heappush(self._heap, (time.time() + delay, next(self._seq), outbox_id, job))
            self._threads = [t for t in self._threads if t.is_alive()]
            self._stop = False
            while len(self._threads) < self.n_workers:
                t = threading.Thread(target=self._run, name="alert-dispatcher", daemon=True)
                t.start()
                self._threads.append(t)
            self._cv.notify_all()

    def recover(self) -> int:
        """Re-queue PENDING rows left by a crashed process (attempt count is kept)."""
        with self.eng.begin() as c:
            rs = c.execute(select(S.outbox).where(S.outbox.c.status == "PENDING")).fetchall()
        for r in rs:
            self._push(r.outbox_id, 0.0, {"address": r.address, "subject": r.subject, "body": r.body, "body_html": r.body_html,
                                          "alert_id": r.alert_id, "role": r.recipient_role, "attempts": r.attempts})
        return len(rs)

    def _run(self):
        while True:
            with self._cv:
                while not self._stop and (not self._heap or self._heap[0][0] > time.time()):
                    self._cv.wait(timeout=max(0.0, self._heap[0][0] - time.time()) if self._heap else 0.5)
                    if not self._heap and self._stop:
                        break
                if self._stop and not self._heap:
                    return
                if not self._heap or self._heap[0][0] > time.time():
                    continue
                _, _, oid, job = heapq.heappop(self._heap)
                self._inflight += 1
            try:
                self._attempt(oid, job)
            except Exception:  # noqa: BLE001 - the worker must survive anything
                log.exception("alert worker error (outbox %s)", oid)
            finally:
                with self._cv:
                    self._inflight -= 1
                    self._cv.notify_all()

    def _attempt(self, oid: int, job: dict):
        n = job["attempts"] + 1
        err = None
        try:
            self.mailer.send(job["address"], job["subject"], job["body"], job["body_html"])
        except Exception as ex:  # noqa: BLE001
            err = _redact(f"{type(ex).__name__}: {ex}")
        final = err is not None and n >= self.max_attempts
        status = "SENT" if err is None else ("FAILED" if final else "PENDING")
        vals = {"attempts": n, "error": err, "status": status}
        if status == "SENT":
            vals["sent_time"] = time.time()
        with self.eng.begin() as c:       # ONE transaction per attempt: outbox row + derived alert status
            c.execute(S.outbox.update().where(S.outbox.c.outbox_id == oid).values(**vals))
            self._refresh_status(job["alert_id"], c)
        if err is None:
            return
        if final:
            log.error("alert %s to %s FAILED after %d attempts: %s", job["alert_id"], job["role"], n, err)
        else:
            log.warning("alert %s to %s attempt %d failed (%s); retrying", job["alert_id"], job["role"], n, err)
            self._push(oid, self.backoff[min(n - 1, len(self.backoff) - 1)], {**job, "attempts": n})

    @staticmethod
    def _refresh_status(alert_id: str, c):
        """alerts.status is derived from the outbox only: any PENDING -> PENDING; else any FAILED -> FAILED; else SENT."""
        st = [x[0] for x in c.execute(select(S.outbox.c.status).where(S.outbox.c.alert_id == alert_id))]
        new = "PENDING" if "PENDING" in st else ("FAILED" if "FAILED" in st else "SENT" if st else "PENDING")
        c.execute(S.alerts.update().where(S.alerts.c.alert_id == alert_id).values(status=new))

    # ---- control
    def flush(self, timeout: float = 30.0) -> bool:
        """Wait until nothing is queued or in flight (tests / shutdown). True if drained."""
        end = time.time() + timeout
        with self._cv:
            while self._heap or self._inflight:
                left = end - time.time()
                if left <= 0:
                    return False
                self._cv.wait(timeout=min(left, 0.2))
        return True

    def close(self, timeout: float = 5.0):
        self.flush(timeout)
        with self._cv:
            self._stop = True
            self._heap.clear()
            self._cv.notify_all()
