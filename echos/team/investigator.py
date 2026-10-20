"""Case Investigator agent. Goal: for a non-NORMAL case, produce a case file a human can act on in one read (a near miss gets a short WATCH file instead).

It is an agent (not a detector): it chooses which check to run next from what it has seen so far, calls bounded tools, keeps a trace of every
step, and posts its result on the bus. It uses NO LLM and trains NO model; it never changes the triage status and never gives medical advice.
Tools: read_detectors, check_history, check_agreement, check_data_caveats (rules over today's detector output and the case history) and two that
COMPUTE on the person's own past data (echos/team/evidence_tools.py, past-only): check_trend (robust z-score against the personal baseline,
slope and direction of the last readings) and check_personal_history (how often this person was outside their usual before and how many of those
days carried a recorded clinical event).
At most investigator.max_steps tool calls per case.

Evidence level (not a probability): STRONG = an absolute rule fired (critical vitals / confirmed fall) or >= 2 independent sources agree;
MODERATE = one model flag with persistence or a repeat; LIMITED = a single model flag, or the data has caveats that weaken it.
Of the computed checks only one may move the level, and only upward (LIMITED -> MODERATE): the person's own earlier deviation days carried a
recorded event. It is the one signal that separated event days from non-event days in scripts/evaluation/investigator_eval.py (CI barely above zero). Trend
and an event-free history did NOT separate them there, so they are listed as context and never change the level."""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import select

from echos.config import load_config
from echos.orchestrator import state as S
from echos.team import bus
from echos.team import evidence_tools as ET

AGENT = "investigator"


def _vit(results):
    return ((results.get("health") or {}).get("vitals")) or {}


class Investigator:
    def __init__(self, eng):
        self.eng = eng
        self.max_steps = int((load_config().get("investigator") or {}).get("max_steps", 7))

    # ---------------------------------------------------------------- tools
    def read_detectors(self, results: dict, decision: dict) -> dict:
        """Which detectors raised this case, by the SAME thresholds triage uses (config triage.vitals_prob; the fixed vitals rule only when
        monitoring is degraded). A detector that merely produced evidence below those thresholds is NOT flagged, so it can neither add an independent source
        nor raise the evidence level."""
        t = load_config()["triage"]
        v, f, q = _vit(results), results.get("fall") or {}, (results.get("quality") or {}).get("evidence", {})
        degraded = bool((results.get("health") or {}).get("degraded"))
        flagged = []
        if "critical_vitals" in (decision.get("urgent_types") or []):
            vit = True
        elif degraded:
            vit = bool(v.get("fixed_rule_triggered"))
        elif "probability" in v:
            vit = bool(v.get("available")) and float(v["probability"]) >= t["vitals_prob"]
        else:
            vit = bool(v.get("flag"))
        if vit:
            flagged.append("vitals")
        if f.get("flag") or (f.get("score") or 0) >= t["fall_prob"]:
            flagged.append("fall")
        return {"flagged": flagged, "vitals_available": bool(v.get("available")),
                "persistent": bool((v.get("persistence") or {}).get("persistent")), "post_fall_inactive": bool((f.get("evidence") or {}).get("post_fall_inactive")),
                "unchecked": q.get("quality_checked") is False, "degraded": degraded,
                "urgent_types": decision.get("urgent_types") or []}

    @staticmethod
    def specifics(results: dict, det: dict) -> dict:
        """The numbers behind the flags, as plain sentences: per vital its value, the person's own usual and the difference (echos/reports/daily/specifics.py), and
        Returns {"vitals": [...], "types": [...], "values": {...}}."""
        from echos.reports.daily import specifics as SP
        from echos.reports.daily import vitals_why as VW
        cv = _vit(results).get("current_vs_baseline") or {}
        values = {k: x["current"] for k, x in cv.items() if x.get("current") is not None}
        bases = {k: x.get("baseline") for k, x in cv.items()}
        zs = {k: x.get("z") for k, x in cv.items()}
        vit = SP.vital_sentences(values, bases, zs) if values and ("vitals" in det["flagged"] or det["urgent_types"]) else []
        types = VW.classify(values, bases, zs=zs, critical="critical_vitals" in det["urgent_types"])["types"] if values else []
        return {"vitals": vit, "types": types, "values": values}

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
        independent = [x for x in det["flagged"] if x in ("vitals", "fall")]
        return {"independent_sources": len(independent), "sources": independent}

    @staticmethod
    def check_data_caveats(det: dict) -> list:
        out = []
        if det["unchecked"]:
            out.append("The data-quality check did not run; treat the values with care.")
        if det["degraded"]:
            out.append("Monitoring was degraded: only fixed vitals thresholds were used.")
        return out

    # ---- computed checks (past-only data of this person; unavailable data is reported as such, never guessed)
    @staticmethod
    def _history_table():
        try:
            from echos.reports.daily.facts import patient_days
            return patient_days()
        except Exception:   # noqa: BLE001 - no built data: the checks say "not available"
            return None

    def check_trend(self, patient_id: str, day: str, results: dict) -> dict:
        pdy = self._history_table()
        cur = {k: v.get("current") for k, v in (_vit(results).get("current_vs_baseline") or {}).items()}
        if pdy is None:
            return {"available": False, "reason": "no history table"}
        g = pdy[(pdy.patient_id == patient_id) & (pdy.has_reading)].sort_values("day")
        d = __import__("pandas").Timestamp(day).normalize()
        out, any_ok = {}, False
        for v in ET.VITALS:
            now = cur.get(v)
            if now is None:
                t = g[g.day == d]
                now = None if t.empty else t.iloc[0][v]
            r = ET.trend_check(g[g.day < d][v].tolist(), None if now is None else float(now), v)
            out[v] = r
            any_ok = any_ok or r["available"]
        return {"available": any_ok, "measures": out, "confirmed": [v for v, r in out.items() if r.get("confirmed")],
                "outside_usual": [v for v, r in out.items() if r.get("outside_usual")]}

    def check_personal_history(self, patient_id: str, day: str) -> dict:
        pdy = self._history_table()
        if pdy is None:
            return {"available": False, "reason": "no history table"}
        h = ET.personal_history(pdy, patient_id, day)
        return {"available": h["prior_reading_days"] >= 5, **h}

    # ---------------------------------------------------------------- near miss
    def watch(self, patient_id: str, day: str, decision: dict, results: dict) -> Optional[dict]:
        """A near miss (no rule fired, but a detector is close to its own alert threshold; the Team Planner called us): a short WATCH file, one step, no hand-over to the
        Coordinator or the Report agent (it is not a changed day, so it never counts towards a clinician review)."""
        from echos.team.planner import detector_scores, nearest, theta
        r, k = nearest(detector_scores(results, load_config()["triage"]))
        if k is None or r <= theta():                  # not a near miss: nothing to write
            return None
        obs = {"detector": k, "ratio": round(r, 3)}
        file = {"patient_id": patient_id, "day": day, "status": "NORMAL", "evidence_level": "WATCH", "reasons": [], "caveats": [], "specifics": [],
                "headline": f"Watch only: no rule fired, but the {k} detector is at {r * 100:.0f}% of its alert threshold.",
                "detectors": {"flagged": [], "urgent_types": [], "nearest": obs}, "history": None, "agreement": None,
                "next_checks": ["Nothing to do now. If the next days stay close to or pass the threshold this becomes a changed day."],
                "trace": [{"step": 1, "tool": "nearest_threshold", "why": "a near miss: which detector is closest to its own alert threshold?", "result": obs}],
                "disclaimer": "Decision support, not a diagnosis."}
        with self.eng.begin() as c:
            S.upsert(c, S.case_file, ["patient_id", "day"], patient_id=patient_id, day=day, file=json.dumps(file), created_ts=S.now())
        return file

    # ---------------------------------------------------------------- the plan loop
    def investigate(self, patient_id: str, day: str, decision: dict, results: dict) -> Optional[dict]:
        status = decision.get("status")
        if status == "NORMAL":
            return self.watch(patient_id, day, decision, results)
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
        trend = hist_p = None
        vit_involved = "vitals" in det["flagged"] or "critical_vitals" in det["urgent_types"]
        if steps < self.max_steps and vit_involved:
            trend = step("check_trend", self.check_trend(patient_id, day, results), "is the reading on a trend away from this person's usual, or a single outlier?")
        if steps < self.max_steps and det["flagged"]:
            hist_p = step("check_personal_history", self.check_personal_history(patient_id, day), "has this person deviated before, and did those days carry a recorded event?")

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
        # Only the signal that separated event days from non-event days in scripts/evaluation/investigator_eval.py (the person's own earlier deviation days that
        # carried an event) may move the level. Trend and "never had an event" did NOT separate them there: they are shown as
        # context for the reader and never change the level.
        supports, weakens, context = [], [], []
        if hist_p and hist_p.get("available") and hist_p["prior_deviation_days"] >= 2 and \
                hist_p["prior_deviation_days_with_event"] >= 0.5 * hist_p["prior_deviation_days"]:
            supports.append(f"{hist_p['prior_deviation_days_with_event']} of this person's {hist_p['prior_deviation_days']} earlier days outside their usual carried a recorded clinical event")
        if trend and trend.get("confirmed"):
            m = trend["measures"][trend["confirmed"][0]]
            context.append(f"{trend['confirmed'][0]} is on a trend away from the usual (z {m['z']}, last three readings moving the same way)")
        elif trend and trend.get("outside_usual") and not absolute:
            context.append("the reading is outside the usual but not on a trend (a single outlier so far)")
        if hist_p and hist_p.get("available") and hist_p["prior_deviation_days"] >= 3 and hist_p["prior_deviation_days_with_event"] == 0:
            context.append(f"this person was outside their usual on {hist_p['prior_deviation_days']} earlier days with no recorded clinical event")
        if supports and level == "LIMITED" and not caveats and not absolute:
            level = "MODERATE"

        parts = []
        if "fall" in det["urgent_types"]:
            parts.append("A fall was detected" + (" and the person has not moved since" if det["post_fall_inactive"] else ""))
        if "critical_vitals" in det["urgent_types"]:
            parts.append("A vital sign is beyond its critical limit")
        sp = self.specifics(results, det)
        if "critical_vitals" in det["urgent_types"] and sp["vitals"]:
            parts[-1] += ": " + sp["vitals"][0].rstrip(".")
        if not parts and "vitals" in det["flagged"]:
            parts.append((sp["vitals"][0].rstrip(".") if sp["vitals"] else "A vital-sign reading differs from this person's usual") + (", and has done so on several readings" if det["persistent"] else ""))
        if "fall" in det["flagged"] and "fall" not in det["urgent_types"]:
            parts.append("A possible fall was seen but not confirmed by inactivity")
        if not parts:
            parts.append("The data could not be fully checked today")
        headline = "; ".join(parts) + "."
        nxt = []
        if "fall" in det["urgent_types"]:
            nxt.append("Contact the person now and confirm they are safe.")
        if "critical_vitals" in det["urgent_types"] or "vitals" in det["flagged"]:
            from echos.reports.daily import specifics as SP
            nxt.extend(SP.vital_actions(sp["types"], sp["values"], critical="critical_vitals" in det["urgent_types"]) or ["Ask for a repeat measurement and share it with the clinician."])
            nxt.append("Share the readings with the clinician.")
        if caveats and not nxt:
            nxt.append("Check that the sensors are working and the readings are being sent.")
        if trend and trend.get("confirmed"):
            nxt.append("The reading has been moving away from the usual over the last readings: repeat it and share the trend with the clinician.")
        if hist and hist["streak_before_today"] >= 2:
            nxt.append(f"This is day {hist['streak_before_today'] + 1} in a row of changes: consider a clinician review.")
        file = {"patient_id": patient_id, "day": day, "status": status, "headline": headline, "evidence_level": level,
                "reasons": decision.get("reasons") or [], "detectors": det, "history": hist, "agreement": agree, "caveats": caveats,
                "specifics": sp["vitals"], "trend": trend, "personal_history": hist_p, "supports": supports, "context": context, "context_note": "trend and event-free history are context only: they did not separate event days in scripts/evaluation/investigator_eval.py",
                "next_checks": nxt, "trace": trace, "disclaimer": "Decision support, not a diagnosis."}
        with self.eng.begin() as c:
            S.upsert(c, S.case_file, ["patient_id", "day"], patient_id=patient_id, day=day, file=json.dumps(file), created_ts=S.now())
        bus.post(self.eng, AGENT, "coordinator", "case_file_ready", patient_id, day, {"status": status, "evidence_level": level, "streak": (hist or {}).get("streak_before_today", 0)})
        bus.post(self.eng, AGENT, "report", "case_file_ready", patient_id, day, {"evidence_level": level})
        return file


def run_investigation(eng, patient_id: str, day: str, decision: dict, results: dict, investigator: Optional["Investigator"] = None,
                      opened_extra: Optional[dict] = None) -> Optional[dict]:
    """The one way triage hands a non-Normal case to the Investigator (replay and live). Posts `case_opened`, runs the investigation and returns the
    case file. A failure never stops the case: it is logged, and the Coordinator still gets a `case_file_ready` hand-over marked
    investigator_failed (no evidence level), so the clinician-review count and the activity trail do not silently skip the day. The report is
    written without a case file."""
    import logging
    try:
        if decision.get("status") != "NORMAL":
            bus.post(eng, "triage", AGENT, "case_opened", patient_id, day, {"status": decision.get("status"), "reasons": (decision.get("reasons") or [])[:4],
                                                                          **(opened_extra or {})})
        return (investigator or Investigator(eng)).investigate(patient_id, day, decision, results)
    except Exception:  # noqa: BLE001 - the case goes on without a case file
        logging.getLogger("echos.investigator").exception("investigation failed for %s %s", patient_id, day)
        try:
            if decision.get("status") != "NORMAL":
                bus.post(eng, AGENT, "coordinator", "case_file_ready", patient_id, day, {"status": decision.get("status"), "evidence_level": None,
                                                                                          "investigator_failed": True})
        except Exception:  # noqa: BLE001
            logging.getLogger("echos.investigator").exception("fallback hand-over failed")
        return None


def load_case_file(eng, patient_id: str, day: str) -> Optional[dict]:
    with eng.begin() as c:
        r = c.execute(select(S.case_file.c.file).where((S.case_file.c.patient_id == patient_id) & (S.case_file.c.day == day))).first()
    return json.loads(r[0]) if r else None
