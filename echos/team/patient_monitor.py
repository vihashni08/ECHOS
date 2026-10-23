"""Patient Monitor agent: one per person. Goal: know what is usual for this person and say how much they need a clinician today.

Belief (its own memory, kept in table patient_agent so a restart loses nothing):
  - personal vitals history (last 28 readings per vital)  -> personal usual and z-score  (NIG baseline, echos/monitor/personal_baseline.py)
  - confirmed vitals events k in n days                     -> personal Bayesian event rate (echos/monitor/risk.py)
  - the last 14 windows of each time slot + confirmed agitation episodes -> agitation probability (echos/monitor/agitation.py)
Decisions:
  - a red-flag reading (NEWS2 red score / BP >= 180/120) -> URGENT message to the Care agent at once (no model involved)
  - after the day's clinician feedback      -> REVIEW_REQUEST to the Review Coordinator with its risks, lift and plain-words reasons
Learning: every FEEDBACK message (confirmed / not confirmed) updates the event rate and the episode history used for the next days.

It never sends email itself and never decides who is reviewed: those are the Care agent's and the Review Coordinator's jobs."""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy import select

from echos.config import load_config
from echos.monitor import agitation as A, cohort, personal_baseline as PB, quality as Q, risk as R, store
from echos.orchestrator import state as S
from echos.team import bus
from echos.team.agent_base import AgentBase

log = logging.getLogger("echos.patient_monitor")
AGENT = "patient_monitor"
VITALS = ("sys", "dia", "hr")
NAMES = {"sys": "systolic", "dia": "diastolic", "hr": "pulse"}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


@dataclass
class PatientMonitor:
    patient_id: str
    vit_hist: dict = field(default_factory=lambda: {v: [] for v in VITALS})
    k: int = 0                       # confirmed vitals event days
    n: int = 0                       # days with feedback
    slot_hist: dict = field(default_factory=dict)      # slot hour -> list of raw feature dicts (last 14)
    windows_seen: int = 0
    episode_windows: list = field(default_factory=list)  # window counter values of confirmed agitation episodes
    days_done: list = field(default_factory=list)        # days scored (last 60)
    feedback_days: list = field(default_factory=list)    # days whose feedback was applied (last 60)
    pending: dict = field(default_factory=dict)           # day -> that day's scores + window counters, until its feedback arrives
    last_vitals_day: str | None = None                    # last day with a BP / pulse reading (data-quality rule: stale vitals)

    # ------------------------------------------------------------ persistence
    def to_json(self) -> str:
        return json.dumps(self.__dict__, default=str)

    @classmethod
    def from_json(cls, s: str) -> "PatientMonitor":
        d = json.loads(s)
        d["slot_hist"] = {str(k): v for k, v in d.get("slot_hist", {}).items()}
        return cls(**d)

    # ------------------------------------------------------------ vitals
    def score_vitals(self, readings: dict, models: store.PatientModels, window: int = 28) -> dict:
        out = {}
        for v in VITALS:
            x = _num(readings.get(v))
            pred = PB.predictive(models.nig[v], np.array(self.vit_hist[v][-window:], dtype=float))
            usual, scale = float(pred.mean()), float(pred.kwds["scale"])
            out[v] = {"value": x, "usual": round(usual, 1), "scale": round(scale, 2),
                      "z": None if x is None else round((x - usual) / scale, 2), "n_past": len(self.vit_hist[v][-window:])}
            if x is not None:
                self.vit_hist[v] = (self.vit_hist[v] + [x])[-window:]
        return out

    # ------------------------------------------------------------ agitation
    def _slot_z(self, slot: str, raw: dict, n: int, min_n: int) -> dict:
        past = self.slot_hist.get(slot, [])[-n:]
        z = {}
        for c in A.RAW:
            vals = np.array([p[c] for p in past], dtype=float)
            if len(vals) >= min_n:
                med = float(np.median(vals))
                iqr = float(np.quantile(vals, 0.75) - np.quantile(vals, 0.25))
                z[f"{c}_z"] = (raw[c] - med) / (iqr + 1)
            else:
                z[f"{c}_z"] = np.nan
        return z

    def score_windows(self, windows: list, models: store.PatientModels) -> list:
        cfg = load_config()["monitor"]["agitation"]
        per_day, days = len(cfg["slot_hours"]), cfg["history_days"]
        out = []
        for w in windows:
            slot = str(int(w["slot"]))
            raw = {c: float(w.get(c, 0) or 0) for c in A.RAW}
            row = {**raw, **self._slot_z(slot, raw, cfg["baseline_slots"], cfg["min_baseline_slots"])}
            for h in (8, 12, 18):
                row[f"slot_{h}"] = int(int(slot) == h)
            recent = [e for e in self.episode_windows if self.windows_seen - e <= days * per_day]
            row["hist_episodes"] = len(recent)
            row["windows_since_episode"] = min(self.windows_seen - 1 - max(self.episode_windows), 999) if self.episode_windows else 999
            df = pd.DataFrame([row])
            p = float(models.agitation.predict_proba(df)[0])
            reasons = models.agitation.top_reasons(pd.Series(row))
            w_idx = self.windows_seen
            self.windows_seen += 1
            silent, usual = Q.silent_window(raw["total"], [h["total"] for h in self.slot_hist.get(slot, [])[-cfg["baseline_slots"]:]], load_config()["monitor"])   # past only
            self.slot_hist[slot] = (self.slot_hist.get(slot, []) + [raw])[-cfg["baseline_slots"]:]
            out.append({"w": w["w"], "slot": int(slot), "p": round(p, 4), "reasons": reasons, "events": int(raw["total"]), "idx": w_idx, "silent": silent,
                        "usual": None if usual is None else round(usual, 1)})
        return out

    # ------------------------------------------------------------ day / feedback
    def on_day(self, day: str, payload: dict, models: store.PatientModels) -> dict:
        readings = payload.get("vitals") or {}
        vit = self.score_vitals(readings, models)
        safety = cohort.urgent_flags(readings)
        attention = cohort.safety_flags(readings)
        wins = self.score_windows(payload.get("windows") or [], models)
        a_max = max([w["p"] for w in wins], default=None)
        has_v = any(vit[v]["value"] is not None for v in VITALS)
        gap = None if has_v else Q.vitals_gap_days(day, self.last_vitals_day)      # stale vitals only when today has no reading
        quality = Q.assess_day(day, wins, gap, load_config()["monitor"])
        if has_v:
            self.last_vitals_day = day
        res = {"quality": quality, "day": day, "vitals": vit, "safety": safety, "attention": attention, "agitation": wins, "agitation_max": a_max,
               "has_vitals": has_v, "n_windows": len(wins), "models": models.fold}
        self.pending[day] = res
        self.days_done = (self.days_done + [day])[-60:]
        return res

    def on_feedback(self, day: str, vitals_event: bool, agitation_windows: list, models: store.PatientModels, horizon: int = 3) -> dict:
        """Clinician / monitoring-team outcome for `day`. Updates the belief, then returns the review request for the Coordinator."""
        if day not in self.feedback_days:
            self.k += int(bool(vitals_event))
            self.n += 1
            idx = {w["w"]: w["idx"] for w in (self.pending.get(day) or {}).get("agitation") or []}
            for w in agitation_windows or []:
                if w in idx:
                    self.episode_windows.append(idx[w])
            self.episode_windows = self.episode_windows[-200:]
            self.feedback_days = (self.feedback_days + [day])[-60:]
        risk = R.PersonalRisk(models.beta, self.k, self.n)
        lo, hi = risk.interval()
        v = risk.forecast(horizon)
        today = self.pending.pop(day, None) or {}
        return {"day": day, "vitals_risk": round(v, 4), "rate": round(risk.rate, 4), "rate_ci95": [round(lo, 4), round(hi, 4)],
                "events_k": self.k, "days_n": self.n, "agitation_risk": today.get("agitation_max"), "reasons": self.reasons(today, risk),
                "urgent": bool(today.get("safety"))}

    def reasons(self, today: dict, risk: R.PersonalRisk) -> list:
        out = []
        for v in VITALS:
            r = (today.get("vitals") or {}).get(v) or {}
            if r.get("z") is not None and abs(r["z"]) >= 2:
                out.append(f"{NAMES[v]} {r['value']:.0f} vs usual {r['usual']:.0f} ({r['z']:+.1f} SD)")
        lo, hi = risk.interval()
        if risk.k:
            out.append(f"{risk.k} confirmed BP/pulse event day(s) in {risk.n} days: personal rate {risk.rate:.0%} (95% CI {lo:.0%}-{hi:.0%})")
        best = max(today.get("agitation") or [], key=lambda w: w["p"], default=None)
        if best and best["p"] >= 0.05:
            label = {8: "night", 12: "morning", 18: "afternoon"}[best["slot"]]
            out.append(f"agitation risk {best['p']:.0%} in the {label} window" + (f": {', '.join(best['reasons'])}" if best["reasons"] else ""))
        q = today.get("quality") or {}
        if q.get("flags"):
            out.append("data check: " + "; ".join(q["flags"]))
        for s in today.get("safety") or []:
            out.append(f"URGENT: {s}")
        for s in today.get("attention") or []:
            out.append(f"outside TIHM check limits: {s}")
        return out


class PatientMonitorAgent(AgentBase):
    """Hosts one PatientMonitor per person (they share a process; each keeps its own state row)."""
    AGENT = AGENT
    KINDS = ("day", "reading")

    def __init__(self, eng, models_dir=None, **kw):
        super().__init__(eng, **kw)
        self.models_dir = str(models_dir or store.DIR)
        self.horizon = load_config()["monitor"]["horizon_days"]
        self.max_wait_s = float(load_config()["review"].get("wait_s", 20))
        self.hard_wait_s = float(load_config()["review"].get("max_wait_s", 300))

    def _load(self, pid: str) -> PatientMonitor:
        with self.eng.begin() as c:
            s = c.execute(select(S.patient_agent.c.state).where(S.patient_agent.c.patient_id == pid)).scalar()
        return PatientMonitor.from_json(s) if s else PatientMonitor(pid)

    def _save(self, c, pm: PatientMonitor, event_id: int | None = None) -> None:
        if event_id is None:     # feedback: keep the last event id
            event_id = c.execute(select(S.patient_agent.c.last_event_id).where(S.patient_agent.c.patient_id == pm.patient_id)).scalar() or 0
        S.upsert(c, S.patient_agent, ["patient_id"], patient_id=pm.patient_id, state=pm.to_json(), last_event_id=event_id, updated_ts=S.now())

    def _done(self, event_id: int) -> bool:
        with self.eng.begin() as c:
            return c.execute(select(S.agent_output.c.event_id).where((S.agent_output.c.event_id == event_id) & (S.agent_output.c.agent_id == AGENT))).first() is not None

    def _outcome_due(self, pid: str, pm: PatientMonitor, day: str) -> bool:
        """True when an earlier day of this patient has a recorded outcome whose feedback has not been applied yet. The next day then waits
        (so its history features include it, exactly as in the offline evaluation). Live mode has no outcome events and never waits."""
        earlier = [d for d in pm.pending if d < day]
        if not earlier:
            return False
        with self.eng.begin() as c:
            return c.execute(select(S.event_log.c.event_id).where((S.event_log.c.kind == "outcome") & (S.event_log.c.patient_id == pid)
                                                                  & S.event_log.c.day.in_(earlier))).first() is not None

    def handle_event(self, ev) -> bool:
        """Returns False when the event must wait (see _outcome_due); the cursor then stays on it."""
        if self._done(ev.event_id):          # idempotent: a restart re-reads events it already handled
            return True
        pid, payload = ev.patient_id, json.loads(ev.payload or "{}")
        pm = self._load(pid)
        if ev.kind == "day" and self._outcome_due(pid, pm, ev.day):
            if not self.waited_enough(ev.event_id, self.max_wait_s, self.hard_wait_s, "care"):
                return False
            log.warning("scoring %s %s without the previous day's feedback", pid, ev.day)
        models = store.for_patient(pid, self.models_dir)
        if ev.kind == "reading":              # a single reading (live device / demo injection): safety + personal z only
            res = {"day": ev.day, "vitals": pm.score_vitals(payload, models), "safety": cohort.urgent_flags(payload),
                   "attention": cohort.safety_flags(payload), "reading": True, "ts": ev.ts}
        else:
            res = pm.on_day(ev.day, payload, models)
        if res["safety"]:                     # URGENT first: the message goes out before anything is written (a crash re-sends; Care dedups)
            bus.post(self.eng, AGENT, "care", "urgent", pid, ev.day, {"event_id": ev.event_id, "event_time": ev.ts, "reasons": res["safety"],
                                                                     "vitals": res["vitals"]})
        status = "URGENT" if res["safety"] else "PENDING"
        with self.eng.begin() as c:
            self._save(c, pm, ev.event_id)
            S.insert_ignore(c, S.agent_output, event_id=ev.event_id, agent_id=AGENT, patient_id=pid, day=ev.day, result=S.j(res), done_ts=S.now())
            prev = c.execute(select(S.case_state.c.status).where((S.case_state.c.patient_id == pid) & (S.case_state.c.day == ev.day))).scalar()
            if prev != "URGENT":
                S.upsert(c, S.case_state, ["patient_id", "day"], patient_id=pid, day=ev.day, status=status, data_gap=bool((not res.get("has_vitals", True) and not res.get("n_windows")) or (res.get("quality") or {}).get("device_problem")),
                         days_since_vitals=None, reasons=S.j(res["safety"]), report=None, updated_ts=S.now())
        self.done_waiting(ev.event_id)
        return True

    def handle_feedback(self, m: dict) -> bool:
        """Returns False (message left for later) when the day itself has not been scored yet."""
        pid, day, pl = m["patient_id"], m["day"], m["payload"]
        pm = self._load(pid)
        if day not in pm.days_done:
            return False
        req = pm.on_feedback(day, bool(pl.get("vitals_event")), pl.get("agitation_windows") or [], store.for_patient(pid, self.models_dir), self.horizon)
        bus.post(self.eng, AGENT, "review_coordinator", "review_request", pid, day, req)
        with self.eng.begin() as c:
            self._save(c, pm)
            c.execute(S.case_state.update().where((S.case_state.c.patient_id == pid) & (S.case_state.c.day == day) & (S.case_state.c.status != "URGENT"))
                      .values(reasons=S.j(req["reasons"]), updated_ts=S.now()))
        return True

    def _feedback(self) -> int:
        msgs = bus.poll(self.eng, AGENT, ["feedback", "review_decision"], limit=200)
        done = [m["id"] for m in msgs if m["topic"] == "review_decision" or self.handle_feedback(m)]
        bus.done(self.eng, done)
        return len(done)

    def step(self) -> int:
        n = self._feedback()                       # learn first, then score
        for ev in self.next_events(self.KINDS, self.cursor):
            if not self.handle_event(ev):
                break                              # waiting for an earlier day's feedback: keep event order
            self.cursor = ev.event_id
            self.save_cursor(self.cursor)
            n += 1
        return n + self._feedback()
