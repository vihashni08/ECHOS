"""Direction-aware reading of one vitals alert: which value moved most relative to the person's OWN usual, in which direction, and so which finding type
it is (bp_high, bp_low, hr_high, hr_low, temperature, weight_change; 'vitals_news2' when no single reading is outside the usual range).
Pure functions of the stored values / baselines / personal bands. No model probability or score is ever read or returned."""
from __future__ import annotations

from typing import Optional

from echos.config import load_config

NAMES = {"sys": "systolic", "dia": "diastolic", "hr": "heart rate", "temp": "temperature", "weight": "weight"}
UNITS = {"sys": "mmHg", "dia": "mmHg", "hr": "bpm", "temp": "C", "weight": "kg"}
NO_SINGLE = "no single reading is outside the usual range; the combination of readings differs from usual"
NO_USUAL = "the person's own usual is not yet established, so these readings cannot be compared with it"
VITALS_TYPES = {"blood_pressure", "vitals_news2", "bp_high", "bp_low", "hr_high", "hr_low", "temperature", "weight_change"}


def _g(x) -> str:
    return f"{round(float(x), 1):g}"


def _type(var: str, delta: float) -> str:
    if var in ("sys", "dia"):
        return "bp_high" if delta > 0 else "bp_low"
    if var == "hr":
        return "hr_high" if delta > 0 else "hr_low"
    return "temperature" if var == "temp" else "weight_change"


def moves(values: dict, baselines: dict, bands: Optional[dict] = None, zs: Optional[dict] = None, k: Optional[float] = None) -> list[dict]:
    """Per variable with a usual: {var, value, base, delta, z, outside}, most-moved first. `bands`: var -> (mean, sd, lo, hi) (weekly personal band);
    `zs`: var -> personal z-score (daily). Without either, nothing counts as outside the usual range."""
    k = k if k is not None else (load_config().get("weekly") or {}).get("band_sd", 2.0)
    out = []
    for v, val in (values or {}).items():
        base = (baselines or {}).get(v)
        if v not in NAMES or val is None or base is None:
            continue
        delta, z, outside = float(val) - float(base), None, False
        b = (bands or {}).get(v)
        if b:
            z, outside = (delta / b[1] if b[1] else None), not (b[2] <= val <= b[3])
        elif zs and zs.get(v) is not None:
            z, outside = float(zs[v]), abs(float(zs[v])) >= k
        out.append({"var": v, "value": float(val), "base": float(base), "delta": delta, "z": z, "outside": outside})
    out.sort(key=lambda m: (not m["outside"], -(abs(m["z"]) if m["z"] is not None else abs(m["delta"]) / (abs(m["base"]) or 1))))
    return out


def critical_breaches(values: dict) -> list[dict]:
    """Fixed safety limits (config triage.critical_vitals) that the reading breaches: [{var, type, limit, side}]."""
    c = load_config()["triage"]["critical_vitals"]
    v = values or {}
    out = []
    if v.get("sys") is not None and v["sys"] >= c["sys_high"]:
        out.append({"var": "sys", "type": "bp_high", "limit": c["sys_high"], "side": "above"})
    if v.get("sys") is not None and v["sys"] < c["sys_low"]:
        out.append({"var": "sys", "type": "bp_low", "limit": c["sys_low"], "side": "below"})
    if v.get("dia") is not None and v["dia"] >= c["dia_high"]:
        out.append({"var": "dia", "type": "bp_high", "limit": c["dia_high"], "side": "above"})
    if v.get("hr") is not None and v["hr"] >= c["hr_high"]:
        out.append({"var": "hr", "type": "hr_high", "limit": c["hr_high"], "side": "above"})
    if v.get("hr") is not None and v["hr"] < c["hr_low"]:
        out.append({"var": "hr", "type": "hr_low", "limit": c["hr_low"], "side": "below"})
    return out


def classify(values: dict, baselines: dict, bands: Optional[dict] = None, zs: Optional[dict] = None, critical: bool = False) -> dict:
    """-> {types: [finding types, first = primary], moves, top (most-moved value outside the usual range or None), lead (top, else the most-moved value), why (plain words)}."""
    mv = moves(values, baselines, bands, zs)
    top = next((m for m in mv if m["outside"]), None)
    br = critical_breaches(values) if critical else []
    types: list[str] = []
    for t in [b["type"] for b in br] + ([_type(top["var"], top["delta"])] if top else []):
        if t not in types:
            types.append(t)
    if not types:
        types = ["vitals_news2"]
    parts = []
    for b in br:
        parts.append(f"{NAMES[b['var']]} {_g(values[b['var']])} {UNITS[b['var']]} is {b['side']} the fixed safety limit of {_g(b['limit'])}")
    if top:
        parts.append(f"{NAMES[top['var']]} {_g(abs(top['delta']))} {UNITS[top['var']]} {'above' if top['delta'] > 0 else 'below'} usual")
    lead = top or (mv[0] if mv else None)
    if not parts and lead:
        parts.append(f"{NAMES[lead['var']]} {_g(abs(lead['delta']))} {UNITS[lead['var']]} {'above' if lead['delta'] > 0 else 'below'} usual, but " + NO_SINGLE)
    elif not parts:
        parts.append(NO_USUAL)
    why = "; ".join(parts)
    return {"types": types, "moves": mv, "top": top, "lead": lead, "why": why[0].upper() + why[1:]}
