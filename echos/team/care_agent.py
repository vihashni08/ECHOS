"""Care agent. Goal: every urgent case and every review the Coordinator assigns reaches a person, and what that person found flows back
to the agents that need it.

It extends the alert lifecycle of the original Care Coordinator (echos/team/coordinator.py: NEW -> SENT -> ACKNOWLEDGED -> RESOLVED,
escalation to a second contact when nobody acknowledges in time) with three v2 duties:
  URGENT message (Patient Monitor)  -> build the fixed-template alert from structured data and queue it to caregiver + clinician at once
  ASSIGN message (Coordinator)      -> open a CLINICIAN_REVIEW task for that patient-day with the monitor's plain-words reasons
  clinician outcome                 -> FEEDBACK to the Patient Monitor (its learning signal); the review row and task record the outcome
In replay the outcome is TIHM's own record of what the monitoring team confirmed that day ('outcome' events); in live use it is the
clinician's confirm / dismiss on a review task (message 'clinician_outcome' posted by the API)."""
from __future__ import annotations

import json
import logging
import time

from sqlalchemy import select

from echos.alerts import payload as P
from echos.orchestrator import state as S
from echos.team import bus
from echos.team.agent_base import AgentBase
from echos.team.coordinator import CareCoordinator

log = logging.getLogger("echos.care")
AGENT = "care"
UNITS = {"sys": "mmHg", "hr": "bpm"}


def urgent_payload(patient_id: str, day: str, m: dict) -> P.AlertPayload:
    v = m.get("vitals") or {}
    lines = []
    s, d = v.get("sys") or {}, v.get("dia") or {}
    if s.get("value") is not None:
        lines.append(P.VitalLine(name="Blood pressure", value=s["value"], value2=d.get("value"), baseline=s.get("usual"), baseline2=d.get("usual"),
                                 unit="mmHg", time=str(m.get("event_time") or "")[:16], age="this reading"))
    h = v.get("hr") or {}
    if h.get("value") is not None:
        lines.append(P.VitalLine(name="Pulse", value=h["value"], baseline=h.get("usual"), unit="bpm", time=str(m.get("event_time") or "")[:16],
                                 age="this reading"))
    aid = f"{patient_id}|{day}|critical_vitals|{m.get('event_id')}"
    return P.AlertPayload(alert_id=aid, alert_type="CRITICAL_VITALS", patient_id=patient_id, age_band=P._age_band(patient_id),
                          event_time=str(m.get("event_time") or f"{day}T12:00:00"), event_id=m.get("event_id"), vitals=lines,
                          vitals_note="; ".join(m.get("reasons") or [])[:160] or None)


class CareAgent(AgentBase):
    AGENT = AGENT
    KINDS = ("outcome",)

    def __init__(self, eng, dispatcher=None, clock=time.time, **kw):
        super().__init__(eng, **kw)
        self.dispatcher = dispatcher
        self.lifecycle = CareCoordinator(eng, dispatcher, clock=clock, agent=AGENT)      # alert lifecycle + escalation (unchanged)

    # ---- URGENT: alert first, nothing else in the way
    def handle_urgent(self, m: dict) -> None:
        p = urgent_payload(m["patient_id"], m["day"], m["payload"])
        queued = bool(self.dispatcher.dispatch(p)) if self.dispatcher is not None else False
        bus.post(self.eng, AGENT, "all", "urgent_sent" if queued else "urgent_duplicate", m["patient_id"], m["day"], {"alert_id": p.alert_id})

    # ---- ASSIGN: a clinician review task with the monitor's reasons
    def _known_outcome(self, patient_id: str, day: str):
        """The outcome already recorded for this patient-day (replay: the TIHM record arrives before the review is assigned)."""
        with self.eng.begin() as c:
            r = c.execute(select(S.agent_message.c.payload).where((S.agent_message.c.sender == AGENT) & (S.agent_message.c.topic == "feedback")
                                                                 & (S.agent_message.c.patient_id == patient_id) & (S.agent_message.c.day == day))
                          .order_by(S.agent_message.c.id.desc())).first()
        return json.loads(r[0]) if r else None

    def handle_assign(self, m: dict) -> None:
        pl = m["payload"]
        reasons = pl.get("reasons") or []
        known = self._known_outcome(m["patient_id"], m["day"])
        done = known is not None
        confirmed = bool(known and (known.get("vitals_event") or known.get("agitation_windows")))
        with self.eng.begin() as c:
            S.insert_ignore(c, S.care_task, patient_id=m["patient_id"], day=m["day"], kind="CLINICIAN_REVIEW", alert_id="", created_by=AGENT,
                            created_ts=S.now(), due_ts=S.now() + 86400, status="DONE" if done else "OPEN", done_ts=S.now() if done else None,
                            title=f"Review #{pl.get('rank')} ({pl.get('reason')}): priority {pl.get('priority')}",
                            detail=("; ".join(reasons)[:850] or "selected to keep every patient seen at least weekly")
                            + ("" if not done else f" | outcome: {'confirmed event' if confirmed else 'no confirmed event'}"))
            if done:
                c.execute(S.review_list.update().where((S.review_list.c.patient_id == m["patient_id"]) & (S.review_list.c.day == m["day"])).values(
                    outcome="CONFIRMED" if confirmed else "NO_EVENT", outcome_ts=S.now()))

    # ---- outcome -> feedback to the Patient Monitor
    def record_outcome(self, patient_id: str, day: str, vitals_event: bool, agitation_windows: list, source: str) -> None:
        confirmed = bool(vitals_event or agitation_windows)
        bus.post(self.eng, AGENT, "patient_monitor", "feedback", patient_id, day,
                 {"vitals_event": bool(vitals_event), "agitation_windows": list(agitation_windows or []), "source": source})
        with self.eng.begin() as c:
            c.execute(S.review_list.update().where((S.review_list.c.patient_id == patient_id) & (S.review_list.c.day == day)).values(
                outcome="CONFIRMED" if confirmed else "NO_EVENT", outcome_ts=S.now()))
            c.execute(S.care_task.update().where((S.care_task.c.patient_id == patient_id) & (S.care_task.c.day == day) & (S.care_task.c.kind == "CLINICIAN_REVIEW")
                                                & (S.care_task.c.status == "OPEN")).values(status="DONE", done_ts=S.now()))

    def step(self) -> int:
        n = 0
        msgs = bus.poll(self.eng, AGENT, ["urgent"], limit=100)          # urgent before anything else
        for m in msgs:
            self.handle_urgent(m)
        bus.done(self.eng, [m["id"] for m in msgs])
        n += len(msgs)
        msgs = bus.poll(self.eng, AGENT, ["assign", "clinician_outcome"], limit=200)
        for m in msgs:
            if m["topic"] == "assign":
                self.handle_assign(m)
            else:
                pl = m["payload"]
                self.record_outcome(m["patient_id"], m["day"], pl.get("vitals_event", False), pl.get("agitation_windows") or [], "clinician")
        bus.done(self.eng, [m["id"] for m in msgs])
        n += len(msgs)
        for ev in self.next_events(self.KINDS, self.cursor):
            pl = json.loads(ev.payload or "{}")
            self.record_outcome(ev.patient_id, ev.day, pl.get("vitals_event", False), pl.get("agitation_windows") or [], "tihm_record")
            self.cursor = ev.event_id
            self.save_cursor(self.cursor)
            n += 1
        try:
            self.lifecycle.step()
        except Exception:  # noqa: BLE001 - lifecycle problems never stop alerts or feedback
            log.exception("alert lifecycle pass failed")
        return n
