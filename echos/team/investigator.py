"""Case Investigator agent. Goal: for a non-NORMAL case, produce a case file a human can act on in one read.

It is an agent (not a detector): it chooses which check to run next from what it has seen so far, calls bounded tools, keeps a trace of every
step, and posts its result on the bus. It uses NO LLM and NO model; it never changes the triage status and never gives medical advice.
Tools: read_detectors, check_history, check_agreement, check_data_caveats. At most investigator.max_steps tool calls per case.

Evidence level (not a probability): STRONG = an absolute rule fired (critical vitals / confirmed fall) or >= 2 independent sources agree;
MODERATE = one model flag with persistence or a repeat; LIMITED = a single model flag, or the data has caveats that weaken it."""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import select

from echos.config import load_config
from echos.orchestrator import state as S
from echos.team import bus

AGENT = "investigator"


def _vit(results):
    return ((results.get("health") or {}).get("vitals")) or {}


def _beh(results):
    return ((results.get("health") or {}).get("behaviour")) or {}


class Investigator:
    def __init__(self, eng):
        self.eng = eng
        self.max_steps = int((load_config().get("investigator") or {}).get("max_steps", 4))

    # ---------------------------------------------------------------- tools
    def read_detectors(self, results: dict, decision: dict) -> dict:
        v, b, f, q = _vit(results), _beh(results), results.get("fall") or {}, (results.get("quality") or {}).get("evidence", {})
        flagged = []
        if v.get("flag") or v.get("fixed_rule_triggered"):
            flagged.append("vitals")
        if b.get("flag"):
            flagged.append("behaviour")
        if f.get("flag") or (f.get("score") or 0) >= load_config()["triage"]["fall_prob"]:
            flagged.append("fall")
        return {"flagged": flagged, "vitals_available": bool(v.get("available")), "behaviour_available": bool(b.get("available")),
                "persistent": bool((v.get("persistence") or {}).get("persistent")), "post_fall_inactive": bool((f.get("evidence") or {}).get("post_fall_inactive")),
                "sensor_gap": bool(q.get("sensor_gap")), "unchecked": q.get("quality_checked") is False, "degraded": bool((results.get("health") or {}).get("degraded")),
                "urgent_types": decision.get("urgent_types") or []}

    def check_history(self, patient_id: str, day: str, n: int = 7) -> dict:
        with self.eng.begin() as c:
            rows = c.execute(select(S.case_state.c.day, S.case_state.c.status, S.case_state.c.reasons).where(
                (S.case_state.c.patient_id == patient_id) & (S.case_state.c.day < day)).order_by(S.case_state.c.day.desc()).limit(n)).fetchall()
        non = [r for r in rows if r.status != "NORMAL"]
        streak = 0
        for r in rows:
            if r.status == "NORMAL":
                break
            streak += 1
        return {"days_seen": len(rows), "non_normal_days": len(non), "streak_before_today": streak,
                "last_status": rows[0].status if rows else None}

    @staticmethod
    def check_agreement(det: dict) -> dict:
        independent = [x for x in det["flagged"] if x in ("vitals", "behaviour", "fall")]
        return {"independent_sources": len(independent), "sources": independent}

    @staticmethod
    def check_data_caveats(det: dict) -> list:
        out = []
        if det["unchecked"]:
            out.append("The data-quality check did not run; treat the values with care.")
        if det["sensor_gap"]:
            out.append("An activity sensor was silent, so routine findings are not reliable for this day.")
        if det["degraded"]:
            out.append("Monitoring was degraded: only fixed vitals thresholds were used.")
        return out

    # ---------------------------------------------------------------- the plan loop
    def investigate(self, patient_id: str, day: str, decision: dict, results: dict) -> Optional[dict]:
        status = decision.get("status")
        if status == "NORMAL":
            return None
        trace, steps = [], 0

        def step(tool, obs, why):
            nonlocal steps
            steps += 1
            trace.append({"step": steps, "tool": tool, "why": why, "result": obs})
            return obs
        det = step("read_detectors", self.read_detectors(results, decision), "start: which detectors raised this case?")
        hist = None
        if steps < self.max_steps and (det["flagged"] or decision.get("data_gap")):
            hist = step("check_history", self.check_history(patient_id, day), "is this new, or part of a pattern over the last days?")
        agree = None
        if steps < self.max_steps and det["flagged"]:
            agree = step("check_agreement", self.check_agreement(det), "do independent sources point the same way?")
        caveats = []
        if steps < self.max_steps:
            caveats = step("check_data_caveats", self.check_data_caveats(det), "is there anything that weakens the evidence?")

        absolute = bool(det["urgent_types"])
        n_src = (agree or {}).get("independent_sources", 0)
        repeat = bool(hist and hist["streak_before_today"] >= 1 and det["flagged"])
        if absolute or n_src >= 2:
            level = "STRONG"
        elif (det["persistent"] or repeat) and not caveats:
            level = "MODERATE"
        else:
            level = "LIMITED"
        if caveats and level == "STRONG" and not absolute:
            level = "MODERATE"

        parts = []
        if "fall" in det["urgent_types"]:
            parts.append("A fall was detected" + (" and the person has not moved since" if det["post_fall_inactive"] else ""))
        if "critical_vitals" in det["urgent_types"]:
            parts.append("A vital sign is beyond its critical limit")
        if not parts and "vitals" in det["flagged"]:
            parts.append("A vital-sign reading differs from this person's usual" + (" and has done so on several readings" if det["persistent"] else ""))
        if "behaviour" in det["flagged"]:
            parts.append("Daily routine differs from this person's usual pattern")
        if "fall" in det["flagged"] and "fall" not in det["urgent_types"]:
            parts.append("A possible fall was seen but not confirmed by inactivity")
        if not parts:
            parts.append("The data could not be fully checked today")
        headline = "; ".join(parts) + "."
        nxt = []
        if "fall" in det["urgent_types"]:
            nxt.append("Contact the person now and confirm they are safe.")
        if "critical_vitals" in det["urgent_types"] or "vitals" in det["flagged"]:
            nxt.append("Ask for a repeat measurement and share it with the clinician.")
        if "behaviour" in det["flagged"]:
            nxt.append("Check in with the person about sleep, appetite and mood today.")
        if caveats and not nxt:
            nxt.append("Check that the sensors are working and the readings are being sent.")
        if hist and hist["streak_before_today"] >= 2:
            nxt.append(f"This is day {hist['streak_before_today'] + 1} in a row of changes: consider a clinician review.")
        file = {"patient_id": patient_id, "day": day, "status": status, "headline": headline, "evidence_level": level,
                "reasons": decision.get("reasons") or [], "detectors": det, "history": hist, "agreement": agree, "caveats": caveats,
                "next_checks": nxt, "trace": trace, "disclaimer": "Decision support, not a diagnosis."}
        with self.eng.begin() as c:
            S.upsert(c, S.case_file, ["patient_id", "day"], patient_id=patient_id, day=day, file=json.dumps(file), created_ts=S.now())
        bus.post(self.eng, AGENT, "coordinator", "case_file_ready", patient_id, day, {"status": status, "evidence_level": level, "streak": (hist or {}).get("streak_before_today", 0)})
        bus.post(self.eng, AGENT, "report", "case_file_ready", patient_id, day, {"evidence_level": level})
        return file


def load_case_file(eng, patient_id: str, day: str) -> Optional[dict]:
    with eng.begin() as c:
        r = c.execute(select(S.case_file.c.file).where((S.case_file.c.patient_id == patient_id) & (S.case_file.c.day == day))).first()
    return json.loads(r[0]) if r else None
