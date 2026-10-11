"""Health Monitoring agent: vitals + behaviour sub-models behind one predict(patient_id, day) -> HealthResult.

The two sub-models (echos/agents/health/vitals.py, behaviour.py) are separate and unchanged. Persistence and drift are
evidence only. Fallback (`fallback_predict`): fixed vitals thresholds from config, no model, flagged `degraded`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from echos.agents.health import behaviour as beh_model
from echos.agents.health import vitals as vit_model
from echos.config import load_config
from echos.schemas import BehaviourPart, HealthResult, VitalsPart

AGENT = "health_monitoring"
DRIFT_NOTE = "explanation_only: drift/CUSUM never triggers an alert"


def _vitals(patient_id: str, day) -> VitalsPart:
    r = vit_model.predict(patient_id, day)
    ev = r.evidence
    if "current_vs_baseline" not in ev:
        return VitalsPart()
    cfg = (load_config().get("health") or {}).get("persistence", {})
    return VitalsPart(available=True, probability=r.score, flag=r.flag, threshold=ev["threshold"],
                      top_features=ev["top_features"], current_vs_baseline=ev["current_vs_baseline"],
                      persistence=vit_model.persistence(patient_id, day, cfg.get("z", 2.0), cfg.get("last_n", 3), cfg.get("need", 2)),
                      fixed_rule_triggered=ev["fixed_rule_triggered"], n_prior_readings=ev["n_prior_readings"])


def _behaviour(patient_id: str, day) -> BehaviourPart:
    r = beh_model.predict(patient_id, day)
    ev = r.evidence
    if "reason" in ev:
        return BehaviourPart()
    try:
        top = beh_model.top_features(patient_id, day)
    except Exception:  # noqa: BLE001 - SHAP is explanation only
        top = []
    return BehaviourPart(available=True, probability=ev["agitation_prob"], flag=bool(ev["agitation_prob"] >= ev["threshold"]),
                         threshold=ev["threshold"], top_features=top, z=ev["z"],
                         drift={"note": DRIFT_NOTE, "drift_score": ev["drift_score"], "cusum": ev["cusum"],
                                "drifted_features": ev["drifted_features"]})


class HealthMonitoringAgent:
    def predict(self, patient_id: str, day) -> HealthResult:
        d = pd.Timestamp(day).normalize()
        return HealthResult(patient_id=patient_id, day=d.date(), vitals=_vitals(patient_id, d), behaviour=_behaviour(patient_id, d))


_AGENT = HealthMonitoringAgent()


def predict(patient_id: str, day) -> HealthResult:
    return _AGENT.predict(patient_id, day)


def fallback_predict(patient_id: str, day, reason: str = "health_monitoring_failed") -> HealthResult:
    """No models: fixed vitals thresholds on the day's readings (config agent1.fixed_rule). Behaviour unavailable."""
    from echos.data.patient_day import load_patient_day
    d = pd.Timestamp(day).normalize()
    rule = load_config()["agent1"]["fixed_rule"]
    pdy = load_patient_day()
    row = pdy[(pdy.patient_id == patient_id) & (pdy.day == d)]
    v = VitalsPart()
    if len(row) and bool(row.iloc[0]["has_reading"]):
        r = row.iloc[0]
        cur = {k: (None if pd.isna(r[k]) else float(r[k])) for k in ("sys", "dia", "hr")}
        hit = bool((cur["sys"] is not None and (cur["sys"] >= rule["sys_high"] or cur["sys"] < rule["sys_low"]))
                   or (cur["dia"] is not None and cur["dia"] >= rule["dia_high"]))
        v = VitalsPart(available=True, probability=1.0 if hit else 0.0, flag=hit, fixed_rule_triggered=hit,
                       current_vs_baseline={k: {"current": c, "baseline": None, "z": None} for k, c in cur.items()})
    return HealthResult(patient_id=patient_id, day=d.date(), vitals=v, degraded=True, degraded_reason=reason)
