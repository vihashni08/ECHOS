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
    """One latest reading with its own timestamp and age relative to the event. BP carries value2/baseline2 (diastolic)."""
    model_config = ConfigDict(validate_assignment=True)
    name: str
    value: Optional[float] = None
    value2: Optional[float] = None
    baseline: Optional[float] = None
    baseline2: Optional[float] = None
    unit: str = ""
    time: str = ""        # when the reading was taken
    age: str = ""         # "2 days before this event"

    @field_validator("name", "unit", "time", "age")
    @classmethod
    def _safe(cls, v):
        return clean(v, 40)


class RoomEvent(BaseModel):
    time: str
    room: str

    @field_validator("time", "room")
    @classmethod
    def _safe(cls, v):
        return clean(v, 50)


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
    vitals_note: Optional[str] = None                                    # e.g. "no recent vitals ..."
    recent_room_events: list[RoomEvent] = Field(default_factory=list)    # last 3 within 6 h of the event, oldest first
    room_note: Optional[str] = None                                      # printed when there are none
    data_quality_note: Optional[str] = None
    checklist: tuple[str, ...] = CHECKLIST
    is_test: bool = False

    @field_validator("patient_id", "age_band", "event_time", "today_status", "alert_id")
    @classmethod
    def _safe(cls, v):
        return clean(v, 80)

    @field_validator("data_quality_note", "vitals_note", "room_note")
    @classmethod
    def _safe_note(cls, v):
        return None if v is None else clean(v, 160)

    @field_validator("checklist")
    @classmethod
    def _fixed(cls, v):
        return CHECKLIST   # the checklist can never be altered by input


def _fmt(v, unit="") -> str:
    return "n/a" if v is None else f"{v:g}{(' ' + unit) if unit else ''}"


def _vital_text(v: VitalLine) -> str:
    val = _fmt(v.value) if v.value2 is None else f"{_fmt(v.value)}/{_fmt(v.value2)}"
    base = "" if v.baseline is None else "; personal baseline " + (_fmt(v.baseline) if v.baseline2 is None else f"{_fmt(v.baseline)}/{_fmt(v.baseline2)}")
    unit = f" {v.unit}" if v.unit else ""
    return f"{v.name} {val}{unit}, {v.age} ({v.time}){base}"


def _vital_rows(p: AlertPayload) -> list[str]:
    out = [p.vitals_note] if p.vitals_note else []
    out += [_vital_text(v) for v in p.vitals]
    return out or ["no vitals available"]


def _room_rows(p: AlertPayload) -> list[str]:
    return [f"{e.time}  {e.room}" for e in p.recent_room_events] or [p.room_note or "none available"]


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
    out += [f"  {r}" for r in _vital_rows(p)]
    out += ["", "Last room-sensor events (within 6 h of the event):"]
    out += [f"  {r}" for r in _room_rows(p)]
    out += ["", "Data quality: " + (p.data_quality_note or "no issues noted"), "", "Checklist:"]
    out += [f"  [ ] {c}" for c in p.checklist]
    out += ["", "ECHOS is a decision-support system, not a diagnostic tool. This message is a fixed template."]
    return "\n".join(out)


def render_html(p: AlertPayload) -> str:
    e = html.escape
    rows = "".join(f"<tr><th align=left>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in _lines(p))
    vit = "".join(f"<li>{e(r)}</li>" for r in _vital_rows(p))
    rooms = "".join(f"<li>{e(r)}</li>" for r in _room_rows(p))
    chk = "".join(f"<li>{e(c)}</li>" for c in p.checklist)
    test = "<p><b>*** TEST MESSAGE - NO REAL EVENT ***</b></p>" if p.is_test else ""
    return (f"<html><body>{test}<h2>{e(TITLE[p.alert_type])}</h2><table>{rows}</table>"
            f"<h3>Latest vitals vs this patient's own baseline</h3><ul>{vit}</ul><h3>Last room-sensor events (within 6 h of the event)</h3><ul>{rooms}</ul>"
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


ROOM_WINDOW_H = 6
VITAL_WINDOW_H = 24


def humanize_age(delta: pd.Timedelta) -> str:
    """'35 minutes before this event', '5 hours ...', '2 days ...' (readings after the event are not used)."""
    m = max(delta.total_seconds(), 0) / 60
    if m < 1:
        return "less than a minute before this event"
    if m < 90:
        n, u = round(m), "minute"
    elif m < 36 * 60:
        n, u = round(m / 60), "hour"
    else:
        n, u = round(m / 1440), "day"
    return f"{n} {u}{'' if n == 1 else 's'} before this event"


def _activity(pid: str):
    if "act" not in _CACHE:
        from echos.config import ROOT, load_config
        a = pd.read_csv(ROOT / load_config()["paths"]["tihm"] / "Activity.csv", usecols=["patient_id", "location_name", "date"],
                        parse_dates=["date"]).sort_values("date")
        _CACHE["act"] = {p: g for p, g in a.groupby("patient_id")}
    return _CACHE["act"].get(pid)


def recent_room_events(pid: str, event_time: str, k: int = 3) -> tuple[list[RoomEvent], Optional[str]]:
    """Last k room events within ROOM_WINDOW_H before the event; if none, (empty, the plain-words note with the last recorded time)."""
    g = _activity(pid)
    t = pd.Timestamp(event_time)
    past = g[g.date <= t] if g is not None else None
    if past is None or past.empty:
        return [], f"No room-sensor activity recorded in the {ROOM_WINDOW_H} hours before the event (last recorded: none on file)"
    inwin = past[past.date >= t - pd.Timedelta(hours=ROOM_WINDOW_H)].tail(k)
    if inwin.empty:
        return [], (f"No room-sensor activity recorded in the {ROOM_WINDOW_H} hours before the event "
                    f"(last recorded: {past.date.iloc[-1].strftime('%Y-%m-%d %H:%M:%S')})")
    return [RoomEvent(time=clean(f"{d.strftime('%Y-%m-%d %H:%M:%S')}, {humanize_age(t - d).replace(' before this event', '')} before", 50), room=clean(r, 30))
            for r, d in zip(inwin.location_name, inwin.date)], None


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


def _physiology(pid: str):
    if "phys" not in _CACHE:
        from echos.config import ROOT, load_config
        d = pd.read_csv(ROOT / load_config()["paths"]["tihm"] / "Physiology.csv", parse_dates=["date"])
        d = d[d.device_type.isin(["Systolic blood pressure", "Diastolic blood pressure", "Heart rate", "Body Temperature"])]
        _CACHE["phys"] = {p: g.sort_values("date") for p, g in d.groupby("patient_id")}
    return _CACHE["phys"].get(pid)


def vitals_lines(eng, pid: str, day: str, event_time: str, decision: dict, results: dict) -> tuple[list[VitalLine], Optional[str]]:
    """Latest BP / heart rate / temperature at or before the event, each with its timestamp, age and personal baseline (when known).
    Returns (lines, note); note says 'no recent vitals' when the newest reading is older than VITAL_WINDOW_H."""
    g, t = _physiology(pid), pd.Timestamp(event_time)
    if g is None or g[g.date <= t].empty:
        return [], "No vitals on file before this event."
    g = g[g.date <= t]
    try:
        from echos.reports.facts import build_day_facts
        base = build_day_facts(eng, pid, day, decision or {}, results or {})["vitals"]
    except Exception:  # noqa: BLE001 - baselines are optional
        base = {}
    b = lambda k: num((base.get(k) or {}).get("baseline"))  # noqa: E731
    out = []
    sys_ = g[g.device_type == "Systolic blood pressure"]
    if len(sys_):
        ts = sys_.date.iloc[-1]
        dia = g[(g.device_type == "Diastolic blood pressure") & (g.date == ts)]
        out.append((ts, VitalLine(name="BP", value=num(sys_.value.iloc[-1], 0), value2=num(dia.value.iloc[-1], 0) if len(dia) else None,
                                  baseline=b("sys"), baseline2=b("dia"), unit="mmHg")))
    for dev, name, key, unit in (("Heart rate", "Heart rate", "hr", "bpm"), ("Body Temperature", "Temperature", "temp", "C")):
        x = g[g.device_type == dev]
        if len(x):
            out.append((x.date.iloc[-1], VitalLine(name=name, value=num(x.value.iloc[-1]), baseline=b(key), unit=unit)))
    lines = []
    for ts, v in out:
        v.time, v.age = ts.strftime("%Y-%m-%d %H:%M"), humanize_age(t - ts)
        lines.append(v)
    newest = max(ts for ts, _ in out) if out else None
    note = None if newest is not None and t - newest <= pd.Timedelta(hours=VITAL_WINDOW_H) else \
        f"No recent vitals (none in the {VITAL_WINDOW_H} hours before this event; newest reading shown below)" if out else "No vitals on file before this event."
    return lines, note


def _set_vitals(p, eng, pid, day, event_time, decision, results):
    p.vitals, p.vitals_note = vitals_lines(eng, pid, day, event_time, decision, results)


def _set_rooms(p, pid, event_time):
    p.recent_room_events, p.room_note = recent_room_events(pid, event_time)


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
                     ("vitals", lambda: _set_vitals(p, eng, patient_id, day, event_time, decision, results)),
                     ("rooms", lambda: _set_rooms(p, patient_id, event_time)),
                     ("quality", lambda: setattr(p, "data_quality_note", quality_note(results)))):
        try:
            fn()
        except Exception as ex:  # noqa: BLE001
            p.data_quality_note = clean(((p.data_quality_note + "; ") if p.data_quality_note else "") + f"{name} unavailable ({type(ex).__name__})", 140)
    return p
