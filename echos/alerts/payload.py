"""AlertPayload: built ONLY from structured data (no free text from any model). A fixed template renders plain text and
simple HTML. Every dynamic value goes through `clean()`, so hostile text in an input (newlines, markup, fake sections)
cannot change the structure of the message. Not a diagnosis: the checklist is fixed and non-prescriptive."""
from __future__ import annotations

import html
import re
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

CHECKLIST = ("Check that the person is responsive.",
             "Call the clinician line.",
             "Check the device / sensors.",
             "Note the time.")
TITLE = {"FALL": "Possible fall detected", "CRITICAL_VITALS": "Critical vitals reading"}
_BAD = re.compile(r"[^A-Za-z0-9 _.:,/%+\-()|]")


def clean(x, n: int = 60) -> str:
    """Any value -> short single-line text from a safe alphabet (no newlines, no markup, no template syntax)."""
    return _BAD.sub("?", str(x))[:n]


def num(x, nd: int = 1) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v or v in (float("inf"), float("-inf")) else round(v, nd)


class VitalLine(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    name: str
    value: Optional[float] = None
    baseline: Optional[float] = None
    unit: str = ""

    @field_validator("name", "unit")
    @classmethod
    def _safe(cls, v):
        return clean(v, 30)


class RoomEvent(BaseModel):
    time: str
    room: str

    @field_validator("time", "room")
    @classmethod
    def _safe(cls, v):
        return clean(v, 30)


class AlertPayload(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    alert_id: str
    alert_type: Literal["FALL", "CRITICAL_VITALS"]
    patient_id: str                       # de-identified id only
    age_band: str = "unknown"             # age band only (no sex, no birth date)
    event_time: str                       # simulated event time, ISO
    event_id: Optional[int] = None
    fall_probability: Optional[float] = None
    peak_impact_g: Optional[float] = None
    post_fall_inactivity: Optional[bool] = None   # None = not assessed
    vitals: list[VitalLine] = Field(default_factory=list)
    today_status: str = "URGENT"
    recent_room_events: list[RoomEvent] = Field(default_factory=list)    # last 3, oldest first
    data_quality_note: Optional[str] = None
    checklist: tuple[str, ...] = CHECKLIST
    is_test: bool = False

    @field_validator("patient_id", "age_band", "event_time", "today_status", "alert_id")
    @classmethod
    def _safe(cls, v):
        return clean(v, 80)

    @field_validator("data_quality_note")
    @classmethod
    def _safe_note(cls, v):
        return None if v is None else clean(v, 140)

    @field_validator("checklist")
    @classmethod
    def _fixed(cls, v):
        return CHECKLIST   # the checklist can never be altered by input


def _fmt(v, unit="") -> str:
    return "n/a" if v is None else f"{v:g}{(' ' + unit) if unit else ''}"


def subject(p: AlertPayload) -> str:
    pre = "[ECHOS TEST] " if p.is_test else "[ECHOS URGENT] "
    return f"{pre}{TITLE[p.alert_type]} - patient {p.patient_id} - {p.event_time[:16].replace('T', ' ')}"


def _lines(p: AlertPayload) -> list[tuple[str, str]]:
    """(label, value) rows in fixed order; the same rows feed text and HTML."""
    rows = [("Alert", TITLE[p.alert_type]), ("Patient (de-identified id)", p.patient_id), ("Age band", p.age_band),
            ("Event time", p.event_time), ("Today's status", p.today_status)]
    if p.alert_type == "FALL":
        rows += [("Fall probability", _fmt(p.fall_probability)), ("Peak impact", _fmt(p.peak_impact_g, "g")),
                 ("Post-fall inactivity", {True: "yes (person still after impact)", False: "no", None: "not assessed"}[p.post_fall_inactivity])]
    return rows


def render_text(p: AlertPayload) -> str:
    out = []
    if p.is_test:
        out += ["*** TEST MESSAGE - NO REAL EVENT ***", ""]
    out += [f"{k}: {v}" for k, v in _lines(p)]
    out += ["", "Latest vitals vs this patient's own baseline:"]
    out += [f"  {v.name}: {_fmt(v.value, v.unit)} (baseline {_fmt(v.baseline, v.unit)})" for v in p.vitals] or ["  no vitals available"]
    out += ["", "Last room-sensor events:"]
    out += [f"  {e.time}  {e.room}" for e in p.recent_room_events] or ["  none available"]
    out += ["", "Data quality: " + (p.data_quality_note or "no issues noted"), "", "Checklist:"]
    out += [f"  [ ] {c}" for c in p.checklist]
    out += ["", "ECHOS is a decision-support system, not a diagnostic tool. This message is a fixed template."]
    return "\n".join(out)


def render_html(p: AlertPayload) -> str:
    e = html.escape
    rows = "".join(f"<tr><th align=left>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in _lines(p))
    vit = "".join(f"<li>{e(v.name)}: {e(_fmt(v.value, v.unit))} (baseline {e(_fmt(v.baseline, v.unit))})</li>" for v in p.vitals) or "<li>no vitals available</li>"
    rooms = "".join(f"<li>{e(r.time)} {e(r.room)}</li>" for r in p.recent_room_events) or "<li>none available</li>"
    chk = "".join(f"<li>{e(c)}</li>" for c in p.checklist)
    test = "<p><b>*** TEST MESSAGE - NO REAL EVENT ***</b></p>" if p.is_test else ""
    return (f"<html><body>{test}<h2>{e(TITLE[p.alert_type])}</h2><table>{rows}</table>"
            f"<h3>Latest vitals vs this patient's own baseline</h3><ul>{vit}</ul><h3>Last room-sensor events</h3><ul>{rooms}</ul>"
            f"<p>Data quality: {e(p.data_quality_note or 'no issues noted')}</p><h3>Checklist</h3><ol>{chk}</ol>"
            "<p><i>ECHOS is a decision-support system, not a diagnostic tool. This message is a fixed template.</i></p></body></html>")


# ---- builders (structured sources only)
_CACHE: dict = {}


def _age_band(pid: str) -> str:
    if "demo" not in _CACHE:
        from echos.data.loaders import load_demographics
        _CACHE["demo"] = dict(zip(load_demographics().patient_id, load_demographics().age_band))
    m = re.findall(r"\d+", str(_CACHE["demo"].get(pid, "")))
    return f"{m[0]}-{m[1]}" if len(m) >= 2 else "unknown"      # "(80, 90]" -> "80-90"


def recent_room_events(pid: str, event_time: str, k: int = 3) -> list[RoomEvent]:
    if "act" not in _CACHE:
        from echos.config import ROOT, load_config
        a = pd.read_csv(ROOT / load_config()["paths"]["tihm"] / "Activity.csv", usecols=["patient_id", "location_name", "date"],
                        parse_dates=["date"]).sort_values("date")
        _CACHE["act"] = {p: g for p, g in a.groupby("patient_id")}
    g = _CACHE["act"].get(pid)
    if g is None:
        return []
    t = pd.Timestamp(event_time)
    g = g[g.date <= t].tail(k)
    return [RoomEvent(time=clean(d.strftime("%Y-%m-%d %H:%M:%S"), 19), room=clean(r, 30)) for r, d in zip(g.location_name, g.date)]


def quality_note(results: dict) -> Optional[str]:
    q = (results or {}).get("quality")
    if q is None:
        return "data quality output missing"
    qe = q.get("evidence", {})
    if not qe.get("quality_checked", True):
        return "quality unchecked"
    if qe.get("sensor_gap"):
        return f"sensor gap ({qe.get('sensor_gap_reason') or 'activity silent'})"
    return None


def vitals_lines(eng, pid: str, day: str, decision: dict, results: dict) -> list[VitalLine]:
    from echos.reports.facts import build_day_facts
    f = build_day_facts(eng, pid, day, decision, results)["vitals"]
    cur = (((results or {}).get("health") or {}).get("vitals") or {}).get("current_vs_baseline") or {}
    units = {"sys": "mmHg", "dia": "mmHg", "hr": "bpm", "temp": "C", "weight": "kg"}
    names = {"sys": "Systolic BP", "dia": "Diastolic BP", "hr": "Heart rate", "temp": "Temperature", "weight": "Weight"}
    out = []
    for k in ("sys", "dia", "hr", "temp", "weight"):
        v, b = (cur.get(k) or {}).get("current"), (cur.get(k) or {}).get("baseline")
        v = num(v) if v is not None else num(f[k]["value"])
        b = num(b) if b is not None else num(f[k]["baseline"])
        if v is not None:
            out.append(VitalLine(name=names[k], value=v, baseline=b, unit=units[k]))
    return out


def build_payload(eng, alert_id: str, alert_type: str, patient_id: str, day: str, event_time: str, event_id=None,
                  decision: Optional[dict] = None, results: Optional[dict] = None, fall: Optional[dict] = None) -> AlertPayload:
    """Every context lookup is best-effort: a failure leaves that field empty and NEVER stops the alert."""
    decision, results = decision or {}, results or {}
    ev = (fall or {}).get("evidence", {})
    p = AlertPayload(alert_id=alert_id, alert_type=alert_type, patient_id=patient_id, event_time=event_time, event_id=event_id,
                     today_status=decision.get("status", "URGENT"), data_quality_note=None)
    if alert_type == "FALL" and fall:
        p.fall_probability = num(fall.get("score"), 2)
        p.peak_impact_g = num(ev.get("peak_svm_g"), 1)
        p.post_fall_inactivity = bool(ev["post_fall_inactive"]) if "post_fall_inactive" in ev else None
    for name, fn in (("age_band", lambda: setattr(p, "age_band", clean(_age_band(patient_id)))),
                     ("vitals", lambda: setattr(p, "vitals", vitals_lines(eng, patient_id, day, decision, results))),
                     ("rooms", lambda: setattr(p, "recent_room_events", recent_room_events(patient_id, event_time))),
                     ("quality", lambda: setattr(p, "data_quality_note", quality_note(results)))):
        try:
            fn()
        except Exception as ex:  # noqa: BLE001
            p.data_quality_note = clean(((p.data_quality_note + "; ") if p.data_quality_note else "") + f"{name} unavailable ({type(ex).__name__})", 140)
    return p
