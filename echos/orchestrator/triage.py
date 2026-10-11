"""Rule-based triage. Pure function, no I/O, no model: thresholds come from config.yaml (`triage:`).

Threshold choice (from validation metrics, see scripts/choose_thresholds.py and reports/metrics/triage_thresholds.json):
- Inputs: quality (Agent 0), health (HealthResult: vitals + behaviour sub-models), fall.
- MONITOR thresholds come from scripts/choose_monitor_thresholds.py (out-of-fold alert budget):
  vitals_prob <= 1 vitals alert per patient-week; agitation_prob alone keeps behaviour Monitor days <= 10% of patient-days; drift is report evidence only.
- drift_level(): max per-feature CUSUM; used for report evidence (drift_evidence_cusum), never as a trigger.
- fall_prob_high 0.90: OOF sensitivity 0.905 at 0.41 false alarms/h without needing any other evidence.
- fall_prob 0.50 + post_fall_inactive: OOF sensitivity 0.942 at 0.90 FA/h; without inactivity it is only MONITOR.
- critical vitals: absolute clinical limits, independent of any model.
DATA_GAP (Agent 0: sensor gap, quality unchecked/missing, or no vitals for > max_vitals_gap_days) is never NORMAL: it yields MONITOR.
Not a diagnostic tool; the LLM never takes part in these decisions.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from echos.config import load_config
from echos.schemas import TriageStatus


class TriageDecision(BaseModel):
    status: TriageStatus
    reasons: list = Field(default_factory=list)
    data_gap: bool = False
    urgent_types: list = Field(default_factory=list)   # "fall" | "critical_vitals": one immediate alert each


def _num(x) -> Optional[float]:
    return None if x is None else float(x)


def drift_level(drift: dict) -> float:
    """Largest per-feature CUSUM in a HealthResult behaviour.drift dict (0 if absent). Evidence only."""
    return max((float(v) for v in (drift.get("cusum") or {}).values()), default=0.0)


def triage(results: dict, days_since_vitals: int = 0, cfg: Optional[dict] = None) -> TriageDecision:
    """results: {'quality': AgentResult-dict|None, 'health': HealthResult-dict|None, 'fall': AgentResult-dict|None}
    (None = agent output missing)."""
    t = cfg or load_config()["triage"]
    health, fall = results.get("health"), results.get("fall")
    vit = (health or {}).get("vitals") or {}
    beh = (health or {}).get("behaviour") or {}
    urgent, monitor, gap, utypes = [], [], [], []

    # ---- URGENT
    if fall:
        p, inactive = fall["score"], bool(fall["evidence"].get("post_fall_inactive"))
        if p >= t["fall_prob_high"]:
            urgent.append(f"fall p={p:.2f}" + (" + post-fall inactivity" if inactive else ""))
            utypes.append("fall")
        elif p >= t["fall_prob"] and inactive:
            urgent.append(f"fall p={p:.2f} + post-fall inactivity")
            utypes.append("fall")
        elif p >= t["fall_prob"]:
            monitor.append(f"possible fall p={p:.2f} (no inactivity)")
    cv = t["critical_vitals"]
    if vit.get("current_vs_baseline"):
        cur = {k: _num(v["current"]) for k, v in vit["current_vs_baseline"].items()}
        s, d, h = cur.get("sys"), cur.get("dia"), cur.get("hr")
        if (s is not None and (s >= cv["sys_high"] or s < cv["sys_low"])) or (d is not None and d >= cv["dia_high"]) \
                or (h is not None and (h >= cv["hr_high"] or h < cv["hr_low"])):
            urgent.append(f"critical vitals sys={s} dia={d} hr={h}")
            utypes.append("critical_vitals")

    # ---- MONITOR (model probabilities; persistence and drift are report evidence only)
    if health and health.get("degraded"):
        if vit.get("fixed_rule_triggered"):
            monitor.append("vitals beyond fixed thresholds (monitoring degraded)")
    else:
        if vit.get("available") and vit["probability"] >= t["vitals_prob"]:
            monitor.append(f"vitals alert p={vit['probability']:.2f}")
        if beh.get("available") and beh["probability"] >= t["agitation_prob"]:
            monitor.append(f"agitation p={beh['probability']:.2f}")

    # ---- data gaps (Agent 0 output): never NORMAL
    q = results.get("quality")
    qe = (q or {}).get("evidence", {})
    if q is None:
        gap.append("quality agent output missing: data unchecked")
    elif not qe.get("quality_checked", True):
        gap.append("quality unchecked (Agent 0 failed, data passed through)")
    else:
        if qe.get("sensor_gap"):
            gap.append("sensor gap (" + str(qe.get("sensor_gap_reason") or "activity silent") + "): activity not treated as zero")
        if qe.get("days_since_last_reading") is not None:
            days_since_vitals = qe["days_since_last_reading"]
    if health is None:
        gap.append("health monitoring output missing")
    elif health.get("degraded"):
        gap.append("monitoring degraded (fixed vitals thresholds only, no behaviour model)")
    if days_since_vitals > t["max_vitals_gap_days"]:
        gap.append(f"no vitals reading for {days_since_vitals} days")

    if urgent:
        return TriageDecision(status=TriageStatus.URGENT, reasons=urgent + monitor, data_gap=bool(gap), urgent_types=utypes)
    if monitor or gap:
        return TriageDecision(status=TriageStatus.MONITOR, reasons=monitor + [f"DATA_GAP: {g}" for g in gap],
                              data_gap=bool(gap))
    return TriageDecision(status=TriageStatus.NORMAL)
