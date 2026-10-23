"""Light data-quality / sensor-silence rules: a quiet person and a dead sensor look the same in the raw counts, so say which one this might be.

Two rules, no model, nothing is imputed and no score is changed (they only add a flag next to the review reasons and set case_state.data_gap):

  silent window   a window with ZERO sensor events although this person's same-slot usual (median of the previous windows of that slot, past only)
                  is at least `min_usual_events` events. For someone who is normally active at that time, total silence is more likely a device or
                  network problem than a quiet home.
  stale vitals    no BP / pulse reading for more than `max_vitals_gap_days` days since the person's last reading.

A day is a probable DEVICE PROBLEM when at least `silent_windows_per_day` of its windows are silent, or the vitals are stale. This cannot tell a sensor failure
from a person who really left the house or went to hospital: it asks a human to check, it does not decide. TIHM has no ground truth for sensor failures,
so only the firing rate is measured on real data (scripts/evaluation/quality_eval.py); detection is tested on a synthetic outage in the unit tests."""
from __future__ import annotations

import numpy as np

DEFAULTS = {"min_usual_events": 20, "min_history_windows": 4, "silent_windows_per_day": 2, "max_vitals_gap_days": 7}


def cfg_of(cfg: dict | None = None) -> dict:
    return {**DEFAULTS, **((cfg or {}).get("quality") or {})}


def silent_window(total: float, past_totals, cfg: dict | None = None) -> tuple[bool, float | None]:
    """(is_silent, usual): `past_totals` = event counts of the previous windows of the SAME time slot (past only)."""
    c = cfg_of(cfg)
    past = np.asarray([x for x in past_totals if x is not None], dtype=float)
    if len(past) < c["min_history_windows"]:
        return False, None
    usual = float(np.median(past))
    return (float(total) == 0.0 and usual >= c["min_usual_events"]), usual


def vitals_gap_days(day: str, last_vitals_day: str | None) -> int | None:
    """Days since the person's last BP / pulse reading (None if they never had one: unknown, not a gap)."""
    if not last_vitals_day:
        return None
    from datetime import date
    return (date.fromisoformat(day[:10]) - date.fromisoformat(last_vitals_day[:10])).days


def assess_day(day: str, windows: list, gap_days: int | None, cfg: dict | None = None) -> dict:
    """windows = [{'slot', 'silent', 'usual'}, ...] for the day. Returns {'flags': [plain words], 'silent_windows': n, 'vitals_gap_days': d, 'device_problem': bool}."""
    c = cfg_of(cfg)
    silent = [w for w in windows if w.get("silent")]
    names = {8: "night", 12: "morning", 18: "afternoon"}
    flags = [f"no sensor events in the {names.get(int(w['slot']), w['slot'])} window, usual about {w['usual']:.0f}" for w in silent]
    stale = gap_days is not None and gap_days > c["max_vitals_gap_days"]
    if stale:
        flags.append(f"no BP / pulse reading for {gap_days} days")
    device = len(silent) >= c["silent_windows_per_day"] or stale
    if device:
        flags.append("check the sensors / devices before reading this as a change in the person")
    return {"flags": flags, "silent_windows": len(silent), "vitals_gap_days": gap_days, "device_problem": bool(device)}
