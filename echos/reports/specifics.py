"""Specific, number-bearing wording for one finding: WHICH vital (or routine measure), its value, the person's own usual, the difference and its direction.
Pure functions of stored values / baselines / z-scores (no model score, no I/O). Used by the case file (echos/team/investigator.py), the daily template
(echos/reports/agent.py) and, as `evidence.specifics`, by the Gemini prompt: the validator then requires an LLM sentence to quote at least one of these values.
Every number printed here is derived from the inputs by subtraction / rounding, so it is also written into the facts the validator accepts numbers from."""
from __future__ import annotations

from typing import Optional

from echos.reports import vitals_why as VW

NAME = {"sys": "Systolic blood pressure", "dia": "Diastolic blood pressure", "hr": "Heart rate", "temp": "Body temperature", "weight": "Body weight"}
SHORT = {"sys": "systolic", "dia": "diastolic", "hr": "heart rate", "temp": "temperature", "weight": "weight"}
UNIT = {"sys": "mmHg", "dia": "mmHg", "hr": "bpm", "temp": "°C", "weight": "kg"}
ROUTINE = {"night_events": "night-time room-sensor events", "night_bathroom": "night-time bathroom visits", "total_events": "daily room-sensor events"}


def _g(x) -> str:
    return f"{round(float(x), 1):g}"


def vital_findings(values: dict, baselines: dict, zs: Optional[dict] = None) -> list[dict]:
    """One dict per vital that has a usual: {var, name, value, unit, usual, diff, direction, z, outside}, most-moved first (outside the usual range first)."""
    out = []
    for m in VW.moves(values, baselines, zs=zs):
        v = m["var"]
        out.append({"var": v, "name": NAME[v], "value": round(m["value"], 1), "unit": UNIT[v], "usual": round(m["base"], 1), "diff": round(abs(m["delta"]), 1),
                    "direction": "above" if m["delta"] > 0 else "below" if m["delta"] < 0 else "at",
                    "z": None if m["z"] is None else round(abs(m["z"]), 1), "outside": bool(m["outside"])})
    return out


def vital_sentence(f: dict) -> str:
    """'Systolic blood pressure 168 mmHg is 25 mmHg above this person's usual of 143 mmHg (2.4 standard deviations from their own usual: outside their usual range).'"""
    if f["direction"] == "at":
        head = f"{f['name']} {_g(f['value'])} {f['unit']} is at this person's usual of {_g(f['usual'])} {f['unit']}"
    else:
        head = f"{f['name']} {_g(f['value'])} {f['unit']} is {_g(f['diff'])} {f['unit']} {f['direction']} this person's usual of {_g(f['usual'])} {f['unit']}"
    if f["z"] is None:
        return head + "."
    return head + f" ({_g(f['z'])} standard deviations from their own usual: {'outside' if f['outside'] else 'within'} their usual range)."


def vital_sentences(values: dict, baselines: dict, zs: Optional[dict] = None, limit: int = 3) -> list[str]:
    """Sentences for the vitals outside the usual range; if none is outside, for the one moved most (so the reader sees what is close to the edge)."""
    fs = vital_findings(values, baselines, zs)
    sel = [f for f in fs if f["outside"]] or fs[:1]
    return [vital_sentence(f) for f in sel[:limit]]


def routine_sentences(routine: dict, features: Optional[list] = None) -> list[str]:
    """'Night-time bathroom visits 13 against a usual of 4 (9 more).' for the routine measures with a usual; `features` (drifted) are added to the wording."""
    out = []
    for k, nm in ROUTINE.items():
        v = (routine or {}).get(k) or {}
        now, base = v.get("value"), v.get("baseline")
        if now is None or base is None:
            continue
        d = round(abs(float(now) - float(base)), 1)
        rel = "the same as usual" if d == 0 else f"{_g(d)} {'more' if now > base else 'fewer'}"
        out.append(f"{nm[0].upper() + nm[1:]} {_g(now)} against a usual of {_g(base)} ({rel}).")
    return out


# ---- direction-aware things to do today: each names the vital, quotes today's value and says what to record
def vital_actions(types: list, values: dict, critical: bool = False) -> list[str]:
    v = values or {}
    bp = f" (today {_g(v['sys'])}/{_g(v['dia'])} mmHg)" if v.get("sys") is not None and v.get("dia") is not None else ""
    bp_of = f" of {_g(v['sys'])}/{_g(v['dia'])} mmHg" if v.get("sys") is not None and v.get("dia") is not None else ""
    hr = f" (today {_g(v['hr'])} bpm)" if v.get("hr") is not None else ""
    acts = {
        "bp_high": f"Measure blood pressure again this morning and this evening, seated and rested for 5 minutes, and write both readings next to today's reading{bp_of}; ask about headache, dizziness or chest discomfort.",
        "bp_low": f"Measure blood pressure again seated and again after standing for a minute{bp}; ask about dizziness or faintness, and check that they are drinking enough.",
        "hr_high": f"Count the pulse again after 5 minutes of rest{hr}; ask about palpitations, breathlessness or a recent effort, and note the time.",
        "hr_low": f"Count the pulse again after 5 minutes of rest{hr}; ask about dizziness, tiredness or fainting, and note the time.",
        "temperature": "Take the temperature again with the same thermometer in an hour; ask about chills, cough or pain when passing urine.",
        "weight_change": "Weigh again tomorrow morning at the same time, before breakfast and in similar clothing; ask about swelling in the legs, appetite and fluid intake.",
        "vitals_news2": f"Repeat the blood pressure and pulse later today, seated and rested, and write the numbers down{bp or hr}; no single value is outside the usual range, so the point is to see whether the combination persists.",
    }
    out = [acts[t] for t in types if t in acts]
    if critical:
        out.append("Contact the care team or doctor today about this reading.")
    return out[:2]


def routine_actions(features: Optional[list], routine: dict) -> list[str]:
    nb, ne = (routine or {}).get("night_bathroom") or {}, (routine or {}).get("night_events") or {}
    out = []
    if nb.get("value") is not None and nb.get("baseline") is not None and nb["value"] > nb["baseline"]:
        out.append(f"Note how many times they get up at night (night-time bathroom visits {_g(nb['value'])} against a usual of {_g(nb['baseline'])}) and ask whether they feel unwell or thirsty.")
    if ne.get("value") is not None and ne.get("baseline") is not None and ne["value"] > ne["baseline"]:
        out.append(f"Ask how they slept; night-time room-sensor events were {_g(ne['value'])} against a usual of {_g(ne['baseline'])}. Watch for restlessness in the evening and what seems to set it off.")
    if not out:
        out.append("Watch for restlessness or distress, especially in the evening and at night, and note what seems to trigger it.")
    return out[:2]
