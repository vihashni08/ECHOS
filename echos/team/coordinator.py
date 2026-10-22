"""Alert lifecycle used by the Care agent (echos/team/care_agent.py). Goal: every alert is acted on by a human, or someone else is told.

It runs its own loop (a thread inside the runtime), keeps its state in SQLite (so a restart loses nothing) and acts through tools:
  track delivery      NEW -> SENT when the outbox shows a delivered row; if the caregiver mail FAILED it escalates at once
  wait for an ack     deadline = sent time + coordinator.ack_timeout_s[type]
  escalate            no ack by the deadline -> the escalation contact gets the same alert (dispatcher.dispatch_escalation), state ESCALATED
It never delays or suppresses the first alert (that is the dispatcher's job, queued before any report work) and never touches triage."""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from sqlalchemy import func, select

from echos.config import load_config
from echos.orchestrator import state as S
from echos.team import bus, lifecycle

log = logging.getLogger("echos.coordinator")
AGENT = "coordinator"


class CareCoordinator:
    def __init__(self, eng, dispatcher=None, clock=time.time, cfg: Optional[dict] = None, agent: str = AGENT):
        self.eng, self.dispatcher, self.clock = eng, dispatcher, clock
        self.agent = agent      # name on the bus / tasks ('care' when run inside the v2 Care agent)
        self.cfg = cfg or load_config().get("coordinator", {})
        self._stop = threading.Event()
        self._thread = None
        self.last_beat = 0.0
        self._last_prune = 0.0

    # ------------------------------------------------------------ one pass (also called directly by tests with a fake clock)
    def step(self) -> dict:
        n = {"registered": 0, "sent": 0, "escalated": 0}
        n["registered"] = self._register()
        n["sent"] = self._track_delivery()
        n["escalated"] = self._escalate_overdue()
        self._housekeeping()
        self.last_beat = self.clock()
        return n

    def _housekeeping(self) -> None:
        """Once an hour: drop handled bus messages older than bus.keep_s (a bad pass here must never stop the alert lifecycle)."""
        now = self.clock()
        if now - self._last_prune < float(self.cfg.get("prune_every_s", 3600)):
            return
        self._last_prune = now
        try:
            bus.prune(self.eng, float(self.cfg.get("bus_keep_s", 30 * 86400)), now=S.now())
        except Exception:  # noqa: BLE001
            log.exception("bus prune failed")

    def _timeout(self, typ: str) -> float:
        return float((self.cfg.get("ack_timeout_s") or {}).get(typ.upper(), 1800))

    def _register(self) -> int:
        k = 0
        with self.eng.begin() as c:
            unknown = select(S.alerts.c.alert_id, S.alerts.c.patient_id, S.alerts.c.type).where(
                ~S.alerts.c.alert_id.in_(select(S.alert_state.c.alert_id)))        # only alerts the Coordinator has not seen yet
            for a in c.execute(unknown).fetchall():
                S.insert_ignore(c, S.alert_state, alert_id=a.alert_id, patient_id=a.patient_id, type=(a.type or "").upper(), state="NEW", created_ts=self.clock())
                k += 1
        return k

    def _track_delivery(self) -> int:
        k = 0
        with self.eng.begin() as c:
            rows = c.execute(select(S.alert_state).where(S.alert_state.c.state == "NEW")).fetchall()
            for r in rows:
                ob = c.execute(select(S.outbox.c.recipient_role, S.outbox.c.status, S.outbox.c.sent_time).where(S.outbox.c.alert_id == r.alert_id)).fetchall()
                cg = [o for o in ob if o.recipient_role == "caregiver"]
                if any(o.status == "SENT" for o in cg):
                    sent = max([o.sent_time or 0 for o in cg if o.status == "SENT"] or [self.clock()]) or self.clock()
                    c.execute(S.alert_state.update().where(S.alert_state.c.alert_id == r.alert_id).values(
                        state="SENT", sent_ts=sent, deadline_ts=sent + self._timeout(r.type or "")))
                    k += 1
                elif cg and all(o.status == "FAILED" for o in cg):   # cannot reach the caregiver at all: do not wait for a timeout
                    c.execute(S.alert_state.update().where(S.alert_state.c.alert_id == r.alert_id).values(
                        state="SENT", sent_ts=self.clock(), deadline_ts=self.clock(), note="caregiver delivery failed: escalating immediately"))
                    k += 1
        return k

    def _escalate_overdue(self) -> int:
        k, now = 0, self.clock()
        with self.eng.begin() as c:
            due = c.execute(select(S.alert_state).where((S.alert_state.c.state == "SENT") & (S.alert_state.c.deadline_ts <= now))).fetchall()
        stale = float(self.cfg.get("stale_after_s", 6 * 3600))
        for r in due:
            waited = max(0.0, now - (r.sent_ts or r.created_ts or now))
            if waited > stale:         # history from an earlier run / a long outage: never page a second contact about it now
                with self.eng.begin() as c:
                    c.execute(S.alert_state.update().where((S.alert_state.c.alert_id == r.alert_id) & (S.alert_state.c.state == "SENT")).values(
                        state="EXPIRED", note=f"not acknowledged and too old to escalate ({int(waited // 3600)} h)"))
                continue
            if self.dispatcher is not None:
                self.dispatcher.dispatch_escalation(r.alert_id, waited)
            with self.eng.begin() as c:     # conditional update: a concurrent acknowledgement wins
                u = c.execute(S.alert_state.update().where((S.alert_state.c.alert_id == r.alert_id) & (S.alert_state.c.state == "SENT")).values(
                    state="ESCALATED", escalated_ts=now, escalation_to="escalation contact"))
            if u.rowcount:
                k += 1
                bus.post(self.eng, self.agent, "all", "alert_escalated", r.patient_id, "", {"alert_id": r.alert_id, "waited_s": round(waited)})
                log.warning("alert %s escalated after %.0f s without acknowledgement", r.alert_id, waited)
        return k

    # ------------------------------------------------------------ thread
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="coordinator", daemon=True)
        self._thread.start()

    def _run(self):
        poll = float(self.cfg.get("poll_s", 1.0))
        while not self._stop.wait(poll):
            try:
                self.step()
                with self.eng.begin() as c:
                    S.upsert(c, S.agent_heartbeat, ["agent_id"], agent_id=AGENT, ts=S.now(), last_event_id=0, status="running", pid=0)
            except Exception:  # noqa: BLE001 - the agent must survive a bad pass
                log.exception("coordinator pass failed")

    def stop(self):
        self._stop.set()
