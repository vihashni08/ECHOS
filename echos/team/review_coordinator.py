"""Review Coordinator agent. Goal: use the clinical team's limited review time (K reviews a day) where it catches the most, without
leaving anyone unseen for long.

Belief (agent_state): the day each patient was last reviewed and first seen. Inputs: REVIEW_REQUEST messages from the Patient Monitors
(vitals risk, agitation risk, reasons) and a 'day_close' event listing who was active that day.
Decision (echos/monitor/allocation.py): priority = max(v / v_bar, a / a_bar) (lift over each detector's base rate), fairness floor for
patients unseen >= fairness_days, top K. Actions: ASSIGN message to the Care agent per selected patient, a review_list row per patient
(rank, reason, why), case status REVIEW / WATCH / NORMAL (URGENT stays URGENT), and one REVIEW_DECISION message back to the monitors.
If a monitor is late it waits review.wait_s (longer, up to review.max_wait_s, while the Patient Monitor process is down and being restarted),
then allocates with the requests it has and records who was missing."""
from __future__ import annotations

import json
import logging
import time

import pandas as pd
from sqlalchemy import select

from echos.config import load_config
from echos.monitor import allocation as AL, store
from echos.orchestrator import state as S
from echos.team import bus
from echos.team.agent_base import AgentBase

log = logging.getLogger("echos.review_coordinator")
AGENT = "review_coordinator"


class ReviewCoordinatorAgent(AgentBase):
    AGENT = AGENT
    KINDS = ("day_close",)

    def __init__(self, eng, models_dir=None, cfg: dict | None = None, clock=time.time, **kw):
        super().__init__(eng, **kw)
        self.cfg = cfg or load_config()["review"]
        self.rates = store.base_rates(str(models_dir or store.DIR))
        self.clock = clock

    def _requests(self, day: str) -> list:
        msgs = bus.poll(self.eng, AGENT, ["review_request"], limit=1000)
        bus.done(self.eng, [m["id"] for m in msgs if m["day"] < day])   # arrived after their day was allocated: too late to use
        return [m for m in msgs if m["day"] == day]

    def allocate(self, day: str, expected: list, reqs: list) -> list:
        last = self.recall("last_review", {})
        first = self.recall("first_seen", {})
        d = pd.Timestamp(day)
        by_pid = {m["patient_id"]: m for m in reqs}            # the newest request per patient wins
        cands = []
        for pid, m in by_pid.items():
            first.setdefault(pid, day)
            ref = last.get(pid, first[pid])
            p = m["payload"]
            cands.append(AL.Candidate(pid, AL.priority(p.get("vitals_risk"), p.get("agitation_risk"), self.rates["v_bar"], self.rates["a_bar"]),
                                      (d - pd.Timestamp(ref)).days, p.get("vitals_risk"), p.get("agitation_risk")))
        picked = AL.select(cands, int(self.cfg["k_per_day"]), self.cfg.get("fairness_days"), float(self.cfg.get("fair_share", 0.5)))
        chosen = {c.patient_id: (rank, why) for rank, (c, why) in enumerate(picked, 1)}
        rows = []
        with self.eng.begin() as c:
            for cand in sorted(cands, key=lambda x: -x.priority):
                p = by_pid[cand.patient_id]["payload"]
                lv = (cand.vitals_risk or 0) / self.rates["v_bar"]
                la = (cand.agitation_risk or 0) / self.rates["a_bar"]
                prev = c.execute(select(S.case_state.c.status).where((S.case_state.c.patient_id == cand.patient_id) & (S.case_state.c.day == day))).scalar()
                if cand.patient_id in chosen:
                    rank, why = chosen[cand.patient_id]
                    S.upsert(c, S.review_list, ["day", "patient_id"], day=day, patient_id=cand.patient_id, rank=rank, reason=why, priority=cand.priority,
                             vitals_risk=cand.vitals_risk, agitation_risk=cand.agitation_risk, lift_vitals=lv, lift_agitation=la,
                             why=S.j(p.get("reasons") or []), created_ts=S.now())
                    status = "REVIEW"
                    rows.append((cand, rank, why, p))
                else:
                    status = "WATCH" if cand.priority >= float(self.cfg.get("watch_lift", 1.5)) else "NORMAL"
                if prev != "URGENT":
                    c.execute(S.case_state.update().where((S.case_state.c.patient_id == cand.patient_id) & (S.case_state.c.day == day)).values(
                        status=status, updated_ts=S.now()))
        for cand, rank, why, p in rows:
            bus.post(self.eng, AGENT, "care", "assign", cand.patient_id, day, {"rank": rank, "reason": why, "priority": round(cand.priority, 3),
                                                                                "vitals_risk": cand.vitals_risk, "agitation_risk": cand.agitation_risk,
                                                                                "reasons": p.get("reasons") or []})
            last[cand.patient_id] = day
        missing = sorted(set(expected) - set(by_pid))
        bus.post(self.eng, AGENT, "patient_monitor", "review_decision", "", day,
                 {"selected": [c.patient_id for c, *_ in rows], "deferred": sorted(set(by_pid) - set(chosen)), "missing_requests": missing,
                  "k": int(self.cfg["k_per_day"])})
        self.remember("last_review", last)
        self.remember("first_seen", first)
        bus.done(self.eng, [m["id"] for m in reqs])
        if missing:
            log.warning("day %s allocated without requests from %s", day, missing)
        return [c.patient_id for c, *_ in rows]

    def step(self) -> int:
        evs = self.next_events(self.KINDS, self.cursor, limit=1)
        if not evs:
            return 0
        ev = evs[0]
        pl = json.loads(ev.payload or "{}")
        expected = pl.get("patients") or []
        reqs = self._requests(ev.day)
        have = {m["patient_id"] for m in reqs}
        if not set(expected) <= have and not self.waited_enough(ev.event_id, float(self.cfg.get("wait_s", 20)),
                                                                float(self.cfg.get("max_wait_s", 300)), "patient_monitor"):
            return 0                               # monitors still working on this day (or being restarted)
        self.allocate(ev.day, expected, reqs)
        self.done_waiting(ev.event_id)
        self.cursor = ev.event_id
        self.save_cursor(self.cursor)
        return 1
