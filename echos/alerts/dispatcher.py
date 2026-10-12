"""Dispatcher: one dispatch -> caregiver AND clinician. Rows are queued in the outbox (PENDING) and sent by background
worker threads, so triage and the daily report never wait on a mail server. Per recipient: PENDING -> SENT | FAILED, up to
`max_attempts` attempts with backoff (retries are scheduled, not slept: a failing recipient never delays other alerts).
A failure is logged and stays visible in the outbox / alerts tables; it never looks delivered."""
from __future__ import annotations

import heapq
import itertools
import logging
import re
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select

from echos.alerts import payload as P
from echos.alerts.mailer import DryRunMailer, Mailer, recipients as env_recipients
from echos.config import load_config
from echos.orchestrator import state as S

log = logging.getLogger("echos.alerts")


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _mask_address(m) -> str:
    local, _, dom = m.group(0).partition("@")
    return f"{local[:2]}***@{dom}"


def _redact(msg: str) -> str:
    """Error text kept in the outbox and in logs: no SMTP user / password, and any e-mail address masked as `ab***@domain`."""
    for k in ("ECHOS_SMTP_PASSWORD", "ECHOS_SMTP_USER"):
        v = os.environ.get(k)
        if v:
            msg = msg.replace(v, "***")
    return _EMAIL.sub(_mask_address, msg)[:200]


class AlertDispatcher:
    def __init__(self, eng, mailer: Optional[Mailer] = None, to: Optional[dict] = None, max_attempts: Optional[int] = None,
                 backoff_s: Optional[list] = None, cooldown_sim_h: Optional[float] = None, clock=None, threaded: bool = True,
                 retry_window_s: Optional[float] = None, sweep_interval_s: Optional[float] = None, sweep_window_s: Optional[float] = None):
        a = load_config().get("alerts", {})
        self.eng, self.mailer = eng, mailer or DryRunMailer()
        self.to = to if to is not None else env_recipients()
        self.max_attempts = max_attempts or a.get("max_attempts", 3)
        self.backoff = list(backoff_s if backoff_s is not None else a.get("backoff_s", [1, 2, 4]))
        self.cooldown_h = a.get("critical_cooldown_sim_h", 12) if cooldown_sim_h is None else cooldown_sim_h
        self.retry_window = float(retry_window_s if retry_window_s is not None else a.get("retry_window_s", 900))
        self.backoff_cap = float(a.get("backoff_cap_s", 60))
        self.sweep_interval = float(sweep_interval_s if sweep_interval_s is not None else a.get("sweep_interval_s", 300))
        self.sweep_window = float(sweep_window_s if sweep_window_s is not None else a.get("sweep_window_s", 7200))
        self.clock, self.threaded = clock or time.time, threaded      # clock is injectable: tests use a fake clock and drive() instead of threads
        self._sweeper, self._busy = None, set()
        self._heap, self._seq, self._cv = [], itertools.count(), threading.Condition()
        self._inflight, self._stop, self._threads = 0, False, []
        self.n_workers = a.get("workers", 3)   # a slow / hung mail server delays at most one worker, not every alert

    # ---- producer side (fast: two small SQLite writes, then returns)
    def dispatch(self, p: P.AlertPayload) -> bool:
        """Queue one alert for both recipients. False if it is a duplicate (same alert_id) or inside the critical-vitals cooldown."""
        now = self.clock()
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
                                                       event_time=p.event_time, queued_time=now, window_start=now))
                rows.append((r.inserted_primary_key[0], st, {"address": addr, "subject": subj, "body": text, "body_html": html,
                                                            "alert_id": p.alert_id, "role": role, "attempts": 0, "window_start": now}))
            if all(st == "FAILED" for _, st, _ in rows):
                c.execute(S.alerts.update().where(S.alerts.c.alert_id == p.alert_id).values(status="FAILED"))
        for oid, st, job in rows:
            if st == "PENDING":
                self._push(oid, 0.0, job)
            else:
                log.error("alert %s: recipient has no address", p.alert_id)
        return True

    def dispatch_weekly(self, patient_id: str, week: str, subject: str, body: str, body_html: str, pdf_path: str, week_end: str = "",
                        force: bool = False) -> Optional[str]:
        """Queue the weekly report email to the CLINICIAN only, PDF attached. Unique per (patient, ISO week): a second call returns None unless
        force=True (send now), which queues a new 'resend-N' row. Same worker pool and retry/backoff as alerts; send_real=false -> DryRunMailer."""
        base = f"weekly|{patient_id}|{week}"
        addr = self.to.get("clinician", "")
        with self.eng.begin() as c:
            n = c.execute(select(func.count()).select_from(S.outbox).where((S.outbox.c.alert_id == base) | S.outbox.c.alert_id.like(base + "|resend-%"))).scalar()
            if n and not force:
                return None
            aid = base if not n else f"{base}|resend-{n}"
            st, err = ("PENDING", None) if addr else ("FAILED", "no recipient address configured")
            r = c.execute(S.outbox.insert().values(alert_id=aid, recipient_role="clinician", address=addr, channel=self.mailer.channel, subject=subject, body=body,
                                                   body_html=body_html, status=st, attempts=0, error=err, event_time=week_end, queued_time=self.clock(), window_start=self.clock(),
                                                   attachment_name=Path(pdf_path).name, attachment_path=str(pdf_path)))
            oid = r.inserted_primary_key[0]
        if st == "PENDING":
            self._push(oid, 0.0, {"address": addr, "subject": subject, "body": body, "body_html": body_html, "alert_id": aid, "role": "clinician",
                                  "attempts": 0, "attachment": {"name": Path(pdf_path).name, "path": str(pdf_path)}})
        else:
            log.error("weekly report %s: clinician address missing", aid)
        return aid

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
            heapq.heappush(self._heap, (self.clock() + delay, next(self._seq), outbox_id, job))
            if self.threaded:
                self._threads = [t for t in self._threads if t.is_alive()]
                self._stop = False
                while len(self._threads) < self.n_workers:
                    t = threading.Thread(target=self._run, name="alert-dispatcher", daemon=True)
                    t.start()
                    self._threads.append(t)
            self._cv.notify_all()
        if self.threaded and job and not job["alert_id"].startswith("weekly|"):
            self.start_sweeper()

    @staticmethod
    def _job(r) -> dict:
        return {"address": r.address, "subject": r.subject, "body": r.body, "body_html": r.body_html, "alert_id": r.alert_id, "role": r.recipient_role,
                "attempts": r.attempts or 0, "window_start": r.window_start or r.queued_time,
                **({"attachment": {"name": r.attachment_name, "path": r.attachment_path}} if r.attachment_path else {})}

    def recover(self) -> int:
        """Re-queue rows left PENDING / RETRYING by a crashed or restarted process. All state comes from the outbox table (attempts, window start, next
        attempt time), so a restart neither loses a retry nor restarts its window."""
        with self.eng.begin() as c:
            rs = c.execute(select(S.outbox).where(S.outbox.c.status.in_(["PENDING", "RETRYING"]))).fetchall()
        now = self.clock()
        for r in rs:
            self._push(r.outbox_id, max(0.0, (r.next_attempt_time or now) - now), self._job(r))
        return len(rs)

    # ---- sweeper: FAILED alert rows are retried every sweep_interval_s for sweep_window_s after queueing
    def sweep(self) -> int:
        """One pass: FAILED alert rows (never weekly mails) that are older than the sweep interval since their last attempt and younger than the sweep
        window get ONE attempt each. Returns the number of rows attempted."""
        now = self.clock()
        with self.eng.begin() as c:
            rs = c.execute(select(S.outbox).where((S.outbox.c.status == "FAILED") & (S.outbox.c.address != "") & ~S.outbox.c.alert_id.like("weekly|%"))).fetchall()
        n = 0
        for r in rs:
            if now - (r.queued_time or now) > self.sweep_window or now - (r.last_attempt_time or r.queued_time or 0) < self.sweep_interval:
                continue
            self._attempt(r.outbox_id, {**self._job(r), "sweep": True})
            n += 1
        return n

    def start_sweeper(self):
        with self._cv:
            if self._sweeper and self._sweeper.is_alive():
                return
            self._sweeper = threading.Thread(target=self._sweep_loop, name="alert-sweeper", daemon=True)
            self._sweeper.start()

    def _sweep_loop(self):
        while True:
            time.sleep(min(30.0, max(self.sweep_interval / 2, 0.05)))
            if self._stop:
                return
            try:
                self.sweep()
            except Exception:  # noqa: BLE001
                log.exception("alert sweeper error")

    def retry_row(self, outbox_id: int) -> str:
        """On-demand resend of ONE row (POST /api/outbox/{id}/retry). 'sent' rows are never re-sent. Uses this dispatcher's mailer (DryRun unless
        alerts.send_real). Returns the new status: RETRYING (queued), or raises LookupError / ValueError."""
        with self.eng.begin() as c:
            r = c.execute(select(S.outbox).where(S.outbox.c.outbox_id == outbox_id)).first()
            if r is None:
                raise LookupError(f"no outbox row {outbox_id}")
            if r.status == "SENT":
                raise ValueError("already sent: not re-sent (no duplicate email)")
            if not r.address:
                raise ValueError("no recipient address configured for this row")
            c.execute(S.outbox.update().where(S.outbox.c.outbox_id == outbox_id).values(status="RETRYING", window_start=self.clock(), next_attempt_time=self.clock()))
            self._refresh_status(r.alert_id, c)
        job = {**self._job(r), "window_start": self.clock()}
        self._push(outbox_id, 0.0, job)
        return "RETRYING"

    # ---- worker
    def run_due(self) -> int:
        """Process every job that is due now (single-threaded drive for tests with a fake clock)."""
        n = 0
        while True:
            with self._cv:
                if not self._heap or self._heap[0][0] > self.clock():
                    return n
                _, _, oid, job = heapq.heappop(self._heap)
            self._attempt(oid, job)
            n += 1

    def drive(self, until: float, step: float = 30.0) -> None:
        """Fake-clock driver: advance `self.clock` (needs .set / .now) to `until`, running due retries and the sweeper on the way."""
        while self.clock() < until:
            nxt = min(until, self.clock() + step)
            with self._cv:
                if self._heap:
                    nxt = min(nxt, max(self._heap[0][0], self.clock()))
            self.clock.set(nxt)
            self.run_due()
            self.sweep()
            self.run_due()

    def _run(self):
        while True:
            with self._cv:
                while not self._stop and (not self._heap or self._heap[0][0] > self.clock()):
                    self._cv.wait(timeout=max(0.0, self._heap[0][0] - self.clock()) if self._heap else 0.5)
                    if not self._heap and self._stop:
                        break
                if self._stop and not self._heap:
                    return
                if not self._heap or self._heap[0][0] > self.clock():
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
        """One send attempt. Alert mails (FALL / CRITICAL_VITALS): PENDING -> RETRYING with exponential backoff 1, 2, 4, 8 ... capped at backoff_cap_s until
        retry_window_s after the window start, then FAILED (the sweeper tries again later). Weekly mails keep max_attempts with the fixed backoff list.
        A row that is already SENT is never sent again (no duplicate email)."""
        with self._cv:
            if oid in self._busy:
                return
            self._busy.add(oid)
        try:
            with self.eng.begin() as c:
                cur = c.execute(select(S.outbox.c.status, S.outbox.c.attempts).where(S.outbox.c.outbox_id == oid)).first()
            if cur is None or cur.status == "SENT":
                return
            weekly, sweep = job["alert_id"].startswith("weekly|"), bool(job.get("sweep"))
            n = max(job["attempts"], cur.attempts or 0) + 1
            now0, err = self.clock(), None
            try:
                if job.get("attachment"):
                    a = job["attachment"]
                    self.mailer.send(job["address"], job["subject"], job["body"], job["body_html"],
                                     attachments=[{"filename": a["name"], "data": Path(a["path"]).read_bytes(), "mime": "application/pdf"}])
                else:
                    self.mailer.send(job["address"], job["subject"], job["body"], job["body_html"])
            except Exception as ex:  # noqa: BLE001
                err = _redact(f"{type(ex).__name__}: {ex}")
            now = self.clock()
            delay = None
            if err is None:
                status = "SENT"
            elif weekly:
                final = n >= self.max_attempts
                status = "FAILED" if final else "PENDING"
                delay = None if final else self.backoff[min(n - 1, len(self.backoff) - 1)]
            elif sweep:
                status = "FAILED"
            else:
                delay = min(2.0 ** (n - 1), self.backoff_cap)
                if now + delay - (job.get("window_start") or now0) > self.retry_window:
                    status, delay = "FAILED", None
                else:
                    status = "RETRYING"
            vals = {"attempts": n, "error": err, "status": status, "last_attempt_time": now,
                    "next_attempt_time": (now + delay) if delay is not None else None}
            if status == "SENT":
                vals["sent_time"] = now
            with self.eng.begin() as c:       # ONE transaction per attempt: outbox row + derived alert status
                c.execute(S.outbox.update().where(S.outbox.c.outbox_id == oid).values(**vals))
                self._refresh_status(job["alert_id"], c)
            if err is None:
                return
            if status == "FAILED":
                log.error("alert %s to %s FAILED after %d attempts: %s", job["alert_id"], job["role"], n, err)
            else:
                log.warning("alert %s to %s attempt %d failed (%s); retrying in %.0f s", job["alert_id"], job["role"], n, err, delay)
                self._push(oid, delay, {**job, "attempts": n})
        finally:
            with self._cv:
                self._busy.discard(oid)

    @staticmethod
    def _refresh_status(alert_id: str, c):
        """alerts.status is derived from the outbox only: any PENDING -> PENDING; else any FAILED -> FAILED; else SENT."""
        st = [x[0] for x in c.execute(select(S.outbox.c.status).where(S.outbox.c.alert_id == alert_id))]
        new = "PENDING" if ("PENDING" in st or "RETRYING" in st) else ("FAILED" if "FAILED" in st else "SENT" if st else "PENDING")   # RETRYING is an outbox-row state only
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
