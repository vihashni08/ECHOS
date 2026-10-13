"""LiveProcessor: turns raw ingested events into decisions, as they arrive.

  events (activity / vitals / accel / heartbeat)  ->  Quality gate, Vitals detector, Fall detector (on arrival), Behaviour detector (when the day closes)
  -> the SAME rule-based triage as replay (echos/orchestrator/triage.py) -> immediate alert (URGENT) -> Case Investigator -> Report agent.
A day stays open and PROVISIONAL (case_state.provisional) until an event of a later day arrives; the decision is refreshed on every vitals / fall event and,
throttled, on activity. Detectors are scored from the events themselves (echos/live/features.py), never from the pre-built training table; the
training table only supplies the patient's earlier history (their own usual) before the first live day.

One thread, one process: models are loaded here (the API never loads them). Detector failures degrade exactly as in replay: vitals -> fixed thresholds,
quality -> 'unchecked'; both are DATA_GAP, never NORMAL."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select

from echos.agents import data_quality as dq
from echos.config import load_config
from echos.live import features as LF
from echos.live import store as LS
from echos.orchestrator import state as S
from echos.orchestrator.alerts import queue_alerts
from echos.orchestrator.triage import triage as run_triage
from echos.schemas import AgentResult, BehaviourPart, DataQualityResult, HealthResult, VitalsPart
from echos.team import bus
from echos.team.investigator import Investigator

log = logging.getLogger("echos.live")
DETECTORS = ("quality", "health_monitoring", "fall")


class PatientState:
    def __init__(self):
        self.open_day: Optional[str] = None
        self.falls: list = []             # fall detector results of the open day: (event_id, ts, AgentResult dict)
        self.vitals_ev: Optional[tuple] = None   # (event_id, ts) of the newest vitals reading of the open day
        self.sig = None                   # decision signature -> the Investigator re-runs only when it changes
        self.last_decide = 0.0
        self.dirty_fast = False
        self.dirty_light = False
        self.reported_nonnormal = False


class LiveProcessor:
    def __init__(self, eng, dispatcher, reporter, parquet: Optional[pd.DataFrame] = None, fall_model=None, clock=time.time):
        self.eng, self.dispatcher, self.reporter, self.clock = eng, dispatcher, reporter, clock
        self.cfg = load_config()
        self.lc = self.cfg.get("live", {})
        if parquet is None:
            from echos.reports import facts as F
            parquet = F.patient_days()
        self.parquet = parquet
        self.fall_model = fall_model
        self.st: dict[str, PatientState] = {}
        self.cursor = 0
        self.stop = threading.Event()
        self.investigator = Investigator(eng)
        self.gate_on = bool(self.cfg.get("report_gate", {}).get("enabled", True))
        self._fall_ready = False
        from echos.alerts import payload as _P
        _P.LIVE_ENG = eng                  # alert e-mails show the recent vitals / rooms of ingested events too

    # ------------------------------------------------------------ main loop
    def run(self, poll_s: float = 0.2):
        while not self.stop.wait(poll_s):
            try:
                self.step()
                self.heartbeat()
            except Exception:  # noqa: BLE001 - the processor must survive a bad batch
                log.exception("live step failed")

    def heartbeat(self):
        with self.eng.begin() as c:
            for a in DETECTORS:
                S.upsert(c, S.agent_heartbeat, ["agent_id"], agent_id=a, ts=S.now(), last_event_id=self.cursor, status="running", pid=0)

    def step(self) -> int:
        with self.eng.begin() as c:
            evs = c.execute(select(S.event_log).where((S.event_log.c.event_id > self.cursor) & S.event_log.c.kind.in_(LS.LIVE_KINDS))
                            .order_by(S.event_log.c.event_id).limit(500)).fetchall()
        for ev in evs:
            self._on_event(ev)
            self.cursor = ev.event_id
        now = self.clock()
        for pid, st in self.st.items():
            if st.open_day and (st.dirty_fast or (st.dirty_light and now - st.last_decide >= float(self.lc.get("decide_min_interval_s", 2.0)))):
                self.decide(pid, st.open_day, closed=False)
        return len(evs)

    # ------------------------------------------------------------ events
    def _on_event(self, ev):
        st = self.st.setdefault(ev.patient_id, PatientState())
        if st.open_day is None:
            st.open_day = ev.day
        elif ev.day > st.open_day:
            self.close_day(ev.patient_id, st.open_day)
            st.open_day = ev.day
        elif ev.day < st.open_day:
            log.info("late event for closed day %s (%s %s): stored, not re-decided", ev.day, ev.patient_id, ev.kind)
            return
        if ev.kind == "accel":
            r = self._fall(ev)
            if r is not None:
                st.falls.append((ev.event_id, ev.ts, r))
                st.dirty_fast = True
        elif ev.kind == "vitals":
            st.vitals_ev = (ev.event_id, ev.ts)
            st.dirty_fast = True
        else:
            st.dirty_light = True

    # ------------------------------------------------------------ detectors
    def _fall(self, ev) -> Optional[dict]:
        from echos.agents import fall
        from echos.data import sisfall as sf
        pl = json.loads(ev.payload)
        try:
            if not self._fall_ready:
                if self.fall_model:
                    fall.use_model(self.fall_model)
                self._fall_ready = True
            fs, n = int(pl["fs"]), int(self.cfg["agent3"]["window_s"] * int(pl["fs"]))
            a = np.asarray(pl["samples"], dtype=float)
            w = a[:n]
            pk = int(np.argmax(np.sqrt((w ** 2).sum(axis=1))))        # inactivity is measured from the window's OWN max sample, as in training
            r = fall.predict_window(w, fs, following=a[pk + fs:], patient_id=ev.patient_id, day=pd.Timestamp(ev.day).date())
            r.evidence.update({"event_id": ev.event_id, "ts": ev.ts})
            res = r.model_dump(mode="json")
        except Exception:  # noqa: BLE001 - a bad window must not stop the loop
            log.exception("fall detector failed on event %s", ev.event_id)
            return None
        with self.eng.begin() as c:
            S.insert_ignore(c, S.agent_output, event_id=ev.event_id, agent_id="fall", patient_id=ev.patient_id, day=ev.day, result=S.j(res), done_ts=S.now())
        return res

    @staticmethod
    def _fixed_vitals(row: dict, rule: dict) -> VitalsPart:
        cur = {k: (None if pd.isna(row.get(k)) else float(row[k])) for k in ("sys", "dia", "hr")}
        if all(v is None for v in cur.values()):
            return VitalsPart()
        hit = bool((cur["sys"] is not None and (cur["sys"] >= rule["sys_high"] or cur["sys"] < rule["sys_low"])) or (cur["dia"] is not None and cur["dia"] >= rule["dia_high"]))
        return VitalsPart(available=True, probability=1.0 if hit else 0.0, flag=hit, fixed_rule_triggered=hit,
                          current_vs_baseline={k: {"current": c, "baseline": None, "z": None} for k, c in cur.items()})

    def _quality(self, pid: str, day: pd.Timestamp, row: dict, last_act_ts, clock_ts, closed: bool, failed: Optional[str]) -> dict:
        if failed:
            return dq.unchecked(pid, day, failed).model_dump(mode="json")
        gap, why = bool(row["sensor_gap"]), row["sensor_gap_reason"]
        lo, hi = self.lc.get("day_window", [7, 22])
        if not closed and not gap and clock_ts is not None:
            ref = last_act_ts if last_act_ts is not None else day + pd.Timedelta(hours=lo)
            silent = (clock_ts - ref) / pd.Timedelta(hours=1)
            if silent >= float(self.lc.get("silent_hours", 6)) and lo <= clock_ts.hour < hi + 1:
                gap, why = True, "sensor_silent"
        inv = json.loads(row["invalid_json"]) if row.get("invalid_json") else []
        q = DataQualityResult(patient_id=pid, day=day.date(), has_vitals=bool(row["has_reading"]), invalid_values=inv, sensor_gap=gap, sensor_gap_reason=why,
                              days_since_last_reading=int(row["days_since_last_reading"]), completeness=float(dq.completeness(gap, row["has_reading"], len(inv))))
        return AgentResult(patient_id=pid, day=day.date(), agent="quality", score=round(1 - q.completeness, 3), flag=gap or bool(inv),
                           evidence=q.model_dump(mode="json")).model_dump(mode="json")

    # ------------------------------------------------------------ decision
    def decide(self, pid: str, day: str, closed: bool) -> Optional[dict]:
        st = self.st[pid]
        d = pd.Timestamp(day).normalize()
        rows = LS.events_of_day(self.eng, pid, day)
        act, vit = LS.frames_of_day(rows)
        hist = LF.history_for(self.parquet, LS.closed_rows(self.eng, pid), pid, d)
        row = LF.build_row(pid, d, act, vit, hist, closed)
        LS.save_row(self.eng, pid, day, row, closed)
        full = pd.concat([hist, pd.DataFrame([row])], ignore_index=True, sort=False)
        full["day"] = pd.to_datetime(full["day"])
        for col in ("has_activity", "has_reading", "sensor_gap"):
            full[col] = full[col].fillna(False).astype(bool)
        clock_ts = pd.Timestamp(max([r.ts for r in rows] or [day]))
        acts = [r.ts for r in rows if r.kind == "activity"]
        last_act = pd.Timestamp(max(acts)) if acts else None
        # Quality gate
        try:
            quality = self._quality(pid, d, row, last_act, clock_ts, closed, None)
        except Exception as ex:  # noqa: BLE001
            quality = self._quality(pid, d, row, last_act, clock_ts, closed, type(ex).__name__)
        # Vitals + Behaviour (behaviour only on a closed day: it is a day-level model)
        from echos.agents.health import behaviour as bm
        from echos.agents.health import vitals as vm
        degraded, reason, vpart, bpart = False, None, VitalsPart(), BehaviourPart()
        try:
            vr, pers = vm.predict_live(pid, d, full)
            if "current_vs_baseline" in vr.evidence:
                ev = vr.evidence
                vpart = VitalsPart(available=True, probability=vr.score, flag=vr.flag, threshold=ev["threshold"], top_features=ev["top_features"],
                                   current_vs_baseline=ev["current_vs_baseline"], persistence=pers, fixed_rule_triggered=ev["fixed_rule_triggered"],
                                   n_prior_readings=ev["n_prior_readings"])
            if closed:
                br, top = bm.predict_live(pid, d, full)
                if "reason" not in br.evidence:
                    e = br.evidence
                    bpart = BehaviourPart(available=True, probability=e["agitation_prob"], flag=bool(e["agitation_prob"] >= e["threshold"]), threshold=e["threshold"], top_features=top,
                                          z=e["z"], drift={"note": "explanation_only: drift/CUSUM never triggers an alert", "drift_score": e["drift_score"], "cusum": e["cusum"],
                                                           "drifted_features": e["drifted_features"]})
        except Exception as ex:  # noqa: BLE001 - detector down: fixed vitals thresholds, 'monitoring degraded' (same fallback as replay)
            log.exception("health detector failed")
            degraded, reason = True, type(ex).__name__
            vpart = self._fixed_vitals(row, self.cfg["agent1"]["fixed_rule"])
        health = HealthResult(patient_id=pid, day=d.date(), vitals=vpart, behaviour=bpart, degraded=degraded, degraded_reason=reason).model_dump(mode="json")
        fall = max((f for _, _, f in st.falls), key=lambda f: f["score"], default={"score": 0.0, "evidence": {}, "flag": False})
        results = {"quality": quality, "health": health, "fall": fall}
        dsv = int((quality.get("evidence") or {}).get("days_since_last_reading") or 0)
        dec = run_triage(results, dsv).model_dump(mode="json")
        with self.eng.begin() as c:
            S.upsert(c, S.case_state, ["patient_id", "day"], patient_id=pid, day=day, status=dec["status"], data_gap=dec["data_gap"], days_since_vitals=dsv,
                     reasons=S.j(dec["reasons"]),
                     report=None, updated_ts=S.now(), as_of=clock_ts.isoformat(), provisional=not closed)
        st.last_decide, st.dirty_fast, st.dirty_light = self.clock(), False, False
        if dec["status"] == "URGENT":
            queue_alerts(self.eng, self.dispatcher, pid, day, dec, results, vitals_event=st.vitals_ev)       # BEFORE any investigation or report work
        sig = (dec["status"], tuple(dec["reasons"]))
        if dec["status"] != "NORMAL" and sig != st.sig:
            st.sig = sig
            try:
                bus.post(self.eng, "triage", "investigator", "case_opened", pid, day, {"status": dec["status"], "reasons": dec["reasons"][:4], "as_of": clock_ts.isoformat()})
                self.investigator.investigate(pid, day, dec, results)
            except Exception:  # noqa: BLE001
                log.exception("investigation failed")
        if closed or (dec["status"] != "NORMAL" and not st.reported_nonnormal):
            st.reported_nonnormal = st.reported_nonnormal or dec["status"] != "NORMAL"
            self._report(pid, day, dec, results, closed)
        return dec

    def _report(self, pid: str, day: str, dec: dict, results: dict, closed: bool):
        from echos.reports.facts import build_day_facts
        try:
            pdy = LS.live_pdy(self.eng, self.parquet)
            facts = build_day_facts(self.eng, pid, day, dec, results, pdy=pdy)
            use_llm = closed and not (self.gate_on and dec["status"] == "NORMAL")      # an open day gets a template; the LLM wording is for the closed day
            out = self.reporter.daily(facts, defer_llm=True, use_llm=use_llm)
            with self.eng.begin() as c:
                c.execute(S.case_state.update().where((S.case_state.c.patient_id == pid) & (S.case_state.c.day == day)).values(report=str(out["path"].name)))
        except Exception:  # noqa: BLE001 - a report problem never stops triage
            log.exception("report failed for %s %s", pid, day)

    def close_day(self, pid: str, day: str):
        st = self.st[pid]
        self.decide(pid, day, closed=True)
        st.falls, st.vitals_ev, st.sig, st.reported_nonnormal = [], None, None, False
