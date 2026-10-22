"""AlertPayload: built ONLY from structured data (no free text from any model). A fixed template renders plain text and
simple HTML. Every dynamic value goes through `clean()`, so hostile text in an input (newlines, markup, fake sections)
cannot change the structure of the message. Not a diagnosis: the checklist is fixed and non-prescriptive."""
from __future__ import annotations

import html
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CHECKLIST_VITALS = ("Check that the person is awake and responsive.",
                    "Repeat the reading after a few minutes of rest.",
                    "Call the clinician line.",
                    "Note the time and the values.")
TITLE = {"CRITICAL_VITALS": "Red-flag vital sign reading"}
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


class AlertPayload(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    alert_id: str
    alert_type: Literal["CRITICAL_VITALS"] = "CRITICAL_VITALS"
    patient_id: str                       # de-identified id only
    age_band: str = "unknown"             # age band only (no sex, no birth date)
    event_time: str                       # simulated event time, ISO
    event_id: Optional[int] = None
    vitals: list[VitalLine] = Field(default_factory=list)
    today_status: str = "URGENT"
    vitals_note: Optional[str] = None                                    # e.g. "no recent vitals ..."
    data_quality_note: Optional[str] = None
    checklist: tuple[str, ...] = CHECKLIST_VITALS
    is_test: bool = False

    @field_validator("patient_id", "age_band", "event_time", "today_status", "alert_id")
    @classmethod
    def _safe(cls, v):
        return clean(v, 80)

    @field_validator("data_quality_note", "vitals_note")
    @classmethod
    def _safe_note(cls, v):
        return None if v is None else clean(v, 160)

    @model_validator(mode="after")
    def _fixed(self):
        # the checklist can never be altered by input: a fixed list
        if self.checklist != CHECKLIST_VITALS:
            object.__setattr__(self, "checklist", CHECKLIST_VITALS)
        return self


def _fmt(v, unit="") -> str:
    return "n/a" if v is None else f"{v:g}{(' ' + unit) if unit else ''}"


def _vital_text(v: VitalLine) -> str:
    val = _fmt(v.value) if v.value2 is None else f"{_fmt(v.value)}/{_fmt(v.value2)}"
    base = "" if v.baseline is None else "; usual " + (_fmt(v.baseline) if v.baseline2 is None else f"{_fmt(v.baseline)}/{_fmt(v.baseline2)}")
    unit = f" {v.unit}" if v.unit else ""
    return f"{v.name} {val}{unit}, {v.age} ({v.time}){base}"


def _vital_rows(p: AlertPayload) -> list[str]:
    out = [p.vitals_note] if p.vitals_note else []
    out += [_vital_text(v) for v in p.vitals]
    return out or ["no vitals available"]


def subject(p: AlertPayload) -> str:
    pre = "[ECHOS TEST] " if p.is_test else "[ECHOS URGENT] "
    return f"{pre}{TITLE[p.alert_type]} - patient {p.patient_id} - {p.event_time[:16].replace('T', ' ')}"


def _lines(p: AlertPayload) -> list[tuple[str, str]]:
    """(label, value) rows in fixed order; the same rows feed text and HTML."""
    rows = [("Alert", TITLE[p.alert_type]), ("Patient (de-identified id)", p.patient_id), ("Age band", p.age_band),
            ("Event time", p.event_time), ("Today's status", p.today_status)]
    return rows


def render_text(p: AlertPayload, ack_url: Optional[str] = None) -> str:
    out = []
    if p.is_test:
        out += ["*** TEST MESSAGE - NO REAL EVENT ***", ""]
    out += [f"{k}: {v}" for k, v in _lines(p)]
    out += ["", "This reading vs the person's own usual:"]
    out += [f"  {r}" for r in _vital_rows(p)]
    out += ["", "Data quality: " + (p.data_quality_note or "no issues noted"), "", "Checklist:"]
    out += [f"  [ ] {c}" for c in p.checklist]
    if ack_url:
        out += ["", f"I have seen this (acknowledge, no login needed): {ack_url}"]
    out += ["", "ECHOS is a decision-support system, not a diagnostic tool. This message is a fixed template."]
    return "\n".join(out)


def render_html(p: AlertPayload, ack_url: Optional[str] = None) -> str:
    e = html.escape
    rows = "".join(f"<tr><th align=left>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in _lines(p))
    vit = "".join(f"<li>{e(r)}</li>" for r in _vital_rows(p))
    chk = "".join(f"<li>{e(c)}</li>" for c in p.checklist)
    ack = f'<p><a href="{e(ack_url)}">I have seen this</a> (acknowledges the alert; no login needed)</p>' if ack_url else ""
    test = "<p><b>*** TEST MESSAGE - NO REAL EVENT ***</b></p>" if p.is_test else ""
    return (f"<html><body>{test}<h2>{e(TITLE[p.alert_type])}</h2><table>{rows}</table>"
            f"<h3>This reading vs the person's own usual</h3><ul>{vit}</ul>"
            f"<p>Data quality: {e(p.data_quality_note or 'no issues noted')}</p><h3>Checklist</h3><ol>{chk}</ol>{ack}"
            "<p><i>ECHOS is a decision-support system, not a diagnostic tool. This message is a fixed template.</i></p></body></html>")


# ---- builders (structured sources only)
_CACHE: dict = {}


def _age_band(pid: str) -> str:
    if "demo" not in _CACHE:
        from echos.data.loaders import load_demographics
        _CACHE["demo"] = dict(zip(load_demographics().patient_id, load_demographics().age_band))
    from echos.data.age import band_label
    return band_label(_CACHE["demo"].get(pid, ""))      # "(80, 90]" -> "80-90"; the open top band "(90, 110]" -> "90+"
