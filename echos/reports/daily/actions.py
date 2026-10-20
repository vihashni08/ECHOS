"""Structured actions for the daily report (the 'Plan' of an SBAR handover): WHAT to do, WHO does it, BY WHEN, and WHEN TO ESCALATE.

Rule-based and deterministic: the same actions are the template report, the starting point Gemini adapts (it may reword and reprioritise but is
validated against the same facts), and the fallback when the model fails. Every number comes from the day's facts or from config
`triage.critical_vitals` (the escalation thresholds), so the validator accepts them. No medicine or dose advice, no diagnosis.
Priority: NOW (within the hour) > TODAY > WITHIN_48H > THIS_WEEK. Owner: CAREGIVER or CLINICIAN."""
from __future__ import annotations

from typing import Optional

from echos.config import load_config
from echos.reports.daily import specifics as SP

PRIORITIES = ["NOW", "TODAY", "WITHIN_48H", "THIS_WEEK"]
OWNERS = ["CAREGIVER", "CLINICIAN"]
PRIORITY_LABEL = {"NOW": "Right now", "TODAY": "Today", "WITHIN_48H": "Within 2 days", "THIS_WEEK": "This week"}
OWNER_LABEL = {"CAREGIVER": "Caregiver", "CLINICIAN": "Clinician"}
VITAL_TYPES = ("bp_high", "bp_low", "hr_high", "hr_low", "temperature", "weight_change", "vitals_news2", "blood_pressure")


def thresholds() -> dict:
    return dict(load_config()["triage"]["critical_vitals"])


def _g(x) -> str:
    return f"{round(float(x), 1):g}"


def _a(priority: str, owner: str, text: str, escalate_if: str, cites: list, topic: str) -> dict:
    return {"priority": priority, "owner": owner, "text": text, "escalate_if": escalate_if, "cites": list(cites), "topic": topic}


def _escalation(t: str, th: dict) -> str:
    """When this finding needs more than the planned action. Thresholds are the system's own critical limits."""
    if t in ("bp_high", "blood_pressure"):
        return (f"a repeat reading is systolic {th['sys_high']} or higher or diastolic {th['dia_high']} or higher, or they have chest pain, a severe headache, "
                "weakness on one side, trouble speaking or a change in vision: seek urgent medical help")
    if t == "bp_low":
        return (f"a repeat reading is systolic below {th['sys_low']}, or they faint, are newly confused, or look pale and clammy: seek urgent medical help")
    if t == "hr_high":
        return (f"the pulse stays at {th['hr_high']} or more at rest, or comes with chest pain, breathlessness or faintness: seek urgent medical help")
    if t == "hr_low":
        return (f"the pulse stays below {th['hr_low']} at rest, or comes with faintness, chest pain or confusion: seek urgent medical help")
    if t == "temperature":
        return "they are shivering, newly confused, short of breath or hard to wake, or the reading is still abnormal an hour later: contact the care team the same day"
    if t == "weight_change":
        return "they have new swelling in the legs, breathlessness, or are eating and drinking much less: contact the care team the same day"
    if t == "vitals_news2":
        return "any single reading reaches a critical limit, or they look unwell, drowsy or confused: seek urgent medical help"
    return "they seem unwell or you are worried: contact the care team"


def for_flag(f: dict, th: Optional[dict] = None) -> list[dict]:
    """Actions for ONE flag (a dict of the day's facts: id, type, evidence)."""
    th = th or thresholds()
    e, t, i = f["evidence"], f["type"], [f["id"]]
    out: list[dict] = []
    if t in ("fall", "fall_inactivity"):
        at = f" at {e['time']}" if e.get("time") else ""
        still = " and little movement was seen afterwards" if e.get("post_fall_inactive") else ""
        out.append(_a("NOW", "CAREGIVER",
                      f"A possible fall was detected{at}{still}. Go to the person now, or phone them if you are away, and check that they are awake, answering and not in pain. "
                      "If they are on the floor, ask about pain and look for injury before lifting them; keep them warm and stay with them.",
                      "they do not answer, cannot get up, have hit their head, have severe pain, or are newly confused: call the emergency services", i, "fall"))
        out.append(_a("TODAY", "CAREGIVER",
                      "Once they are safe, write down what happened: the time, the place, what they were doing, footwear, lighting and whether they felt dizzy beforehand. "
                      "Tell the clinician, even if there is no injury.", "", i, "fall"))
        out.append(_a("WITHIN_48H", "CLINICIAN",
                      "Review after the fall: ask about injury, dizziness on standing and recent changes, and consider a multifactorial falls-risk assessment "
                      "and a check of lying and standing blood pressure.", "", i, "fall"))
        return out
    if e.get("kind") == "critical":
        v = {k: x for k, x in (e.get("values") or {}).items() if x is not None}
        how = SP.vital_actions([t], v, critical=False)
        out.append(_a("NOW", "CAREGIVER", (how[0] if how else "Repeat the reading seated and at rest.") + " Contact the care team or the doctor today about this reading.",
                      _escalation(t, th), i, t))
        out.append(_a("TODAY", "CLINICIAN", "Review the critical-range reading with the recent trend and decide on a same-day contact with the person or caregiver.", "", i, t))
        return out
    if t in VITAL_TYPES:
        v = {k: x for k, x in (e.get("values") or {}).items() if x is not None}
        how = SP.vital_actions([t], v)
        out.append(_a("TODAY", "CAREGIVER", how[0] if how else "Repeat the reading later today, seated and rested, and write the numbers down.", _escalation(t, th), i, t))
        out.append(_a("WITHIN_48H", "CLINICIAN",
                      "Review this week's home readings against the person's own usual range and decide whether a measurement in clinic or a call is needed.", "", i, t))
        return out
    if t == "data_gap":
        out.append(_a("TODAY", "CAREGIVER",
                      "Take a blood pressure and pulse reading today, and check that the monitor is switched on, in range and have fresh batteries. "
                      "A missing reading does not mean the value is normal.", "", i, "data_gap"))
    return out


def build(facts: dict, case: Optional[dict] = None) -> list[dict]:
    """All actions of a day, de-duplicated, ordered by priority then owner, numbered A1.. ; a quiet day gets one routine action."""
    th, out, seen = thresholds(), [], set()
    for f in facts["flags"]:
        for a in for_flag(f, th):
            k = (a["priority"], a["owner"], a["text"])
            if k not in seen:
                seen.add(k)
                out.append(a)
    if not out:
        out.append(_a("THIS_WEEK", "CAREGIVER", "Nothing needs doing today. Keep to the usual routine: regular meals and drinks, a clear and well-lit path to the bathroom, and the next planned reading.",
                      "", [], "routine"))
    out.sort(key=lambda a: (PRIORITIES.index(a["priority"]), OWNERS.index(a["owner"])))
    for n, a in enumerate(out, 1):
        a["id"] = f"A{n}"
    return out
