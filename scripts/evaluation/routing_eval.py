"""Does the Triage stage-2 routing (nearest-threshold ratio r + cutoff theta, echos/team/planner.py) behave as designed? Offline, read-only, no LLM / PubMed / e-mail.

Data: the 1266 replayed patient-days (data/e2e_replay/echos.db: stored status + detector outputs; out-of-fold scores, 20 patients).
  parity      with the cutoff switched off (theta = 1) the routing equals the previous status-based routing (non-Normal -> Investigator + Coordinator; Urgent -> Coordinator owns
              the alert) on every day; with the configured theta the only change is the near-miss WATCH days (Normal, r > theta): Investigator only, never the Coordinator.
  floor       an Urgent day gets the Investigator and the Coordinator as owner for ANY theta (0, 1, huge).
  sensitivity Normal days that become watch days, per patient-week, for a grid of theta.
Honest reading: theta comes from a call budget (scripts/tuning/choose_routing_threshold.py), not from an estimate of harm. r is a routing score, not a probability.
Usage: PYTHONPATH=. python scripts/evaluation/routing_eval.py  -> reports/metrics/routing_eval.json"""
import collections
import json
import sys
from pathlib import Path

import numpy as np
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from echos.config import load_config           # noqa: E402
from echos.orchestrator import state as S      # noqa: E402
from echos.team.planner import detector_scores, nearest, plan_team, theta   # noqa: E402

DB = ROOT / "data/e2e_replay/echos.db"


def load() -> list:
    eng = S.get_engine(str(DB))
    with eng.begin() as c:
        cs, ao = c.execute(select(S.case_state)).fetchall(), c.execute(select(S.agent_output)).fetchall()
    by = collections.defaultdict(dict)
    for r in ao:
        by[(r.patient_id, r.day)].setdefault(r.agent_id, []).append(json.loads(r.result))
    days = []
    for r in cs:
        d = by[(r.patient_id, r.day)]
        reasons = json.loads(r.reasons or "[]")
        ut = (["fall"] if any(str(x).startswith("fall p=") for x in reasons) else []) + (["critical_vitals"] if any(str(x).startswith("critical vitals") for x in reasons) else [])
        days.append({"decision": {"status": r.status, "reasons": reasons, "data_gap": bool(r.data_gap), "urgent_types": ut},
                     "results": {"quality": (d.get("quality") or [None])[0], "health": (d.get("health_monitoring") or [None])[0],
                                 "fall": max(d.get("fall", []), key=lambda x: x["score"], default=None)}})
    return days


def legacy(status: str) -> set:
    return {"triage", "report"} | ({"investigator", "coordinator"} if status != "NORMAL" else set())


def build() -> dict:
    days, t = load(), load_config()["triage"]
    n, pw = len(days), len(days) / 7.0
    th = theta()
    plans = [plan_team(d["decision"], d["results"]) for d in days]
    off = [plan_team(d["decision"], d["results"], {"theta": 1.0}) for d in days]
    want = [legacy(d["decision"]["status"]) for d in days]
    parity_off = sum(set(p.active_ids) == w for p, w in zip(off, want))
    changed = [(p, d) for p, w, d in zip(plans, want, days) if set(p.active_ids) != w]
    watch = [p for p in plans if p.watch]
    r_norm = [nearest(detector_scores(d["results"], t))[0] for d in days if d["decision"]["status"] == "NORMAL"]
    urgent = [d for d in days if d["decision"]["status"] == "URGENT"]
    floor_ok = {str(x): all(plan_team(d["decision"], d["results"], {"theta": x}).has("investigator") and {a.id: a.role for a in plan_team(d["decision"], d["results"], {"theta": x}).agents}["coordinator"] == "owner"
                            for d in urgent) for x in (0.0, 1.0, 1e9)}
    grid = []
    for x in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        pl = [plan_team(d["decision"], d["results"], {"theta": x}) for d in days]
        k = sum(p.watch for p in pl)
        grid.append({"theta": x, "watch_days": k, "watch_days_per_patient_week": round(k / pw, 3), "mean_team_size": round(sum(p.team_size for p in pl) / n, 3)})
    return {"patient_days": n, "days_by_status": dict(collections.Counter(d["decision"]["status"] for d in days)), "theta": th,
            "parity_with_previous_routing": {"days_equal_with_cutoff_off": parity_off, "of": n, "all_equal_with_cutoff_off": parity_off == n,
                                              "days_changed_with_configured_theta": len(changed),
                                              "changed_are_all_normal_watch_days": all(d["decision"]["status"] == "NORMAL" and p.watch for p, d in changed),
                                              "agents_added_on_changed_days": dict(collections.Counter(a for p, d in changed for a in set(p.active_ids) - legacy("NORMAL")))},
            "watch": {"days": len(watch), "per_patient_week": round(len(watch) / pw, 3), "coordinator_called": sum(p.has("coordinator") for p in watch),
                      "note": "each watch day writes a short watch file (Investigator only); no alert, no Coordinator, template report"},
            "normal_days": len(r_norm), "r_normal_quantiles_50_90_99_max": np.quantile(r_norm, [0.5, 0.9, 0.99, 1.0]).round(3).tolist(),
            "sensitivity": grid, "safety_floor": {"urgent_days": len(urgent), "investigator_and_owner_coordinator_for_any_theta": floor_ok}}


if __name__ == "__main__":
    res = build()
    (ROOT / "reports/metrics/routing_eval.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("theta", "parity_with_previous_routing", "watch", "safety_floor")}), "\n", *[str(g) + "\n" for g in res["sensitivity"]])
