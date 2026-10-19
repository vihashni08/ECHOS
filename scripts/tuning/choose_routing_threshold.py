"""Choose the Triage stage-2 cutoff theta by a CALL BUDGET (label-free); same style as scripts/tuning/choose_monitor_thresholds.py.

Per replayed patient-day r = max_k p_k / tau_k (nearest-threshold ratio, echos/team/planner.py). On every day where NO rule fired (status NORMAL: the near-miss
days) a watch call is a false call. theta = the smallest value such that false calls <= B per patient-week (patient-weeks = ALL patient-days / 7), i.e. the
(1 - B*pw/n_normal) quantile of r on Normal days. theta is in plain units: a fraction of each detector's own alert threshold.
Also: patient-level bootstrap (resample patients) of theta at the default budget; a weak cross-check against Labels.csv (sensitivity, not the choice).
Data: data/e2e_replay/echos.db (read-only; stored status + out-of-fold detector outputs). Offline: no LLM / PubMed / e-mail.
Usage: PYTHONPATH=. python scripts/tuning/choose_routing_threshold.py  -> reports/metrics/routing_thresholds.json (then set config `planner.theta` from it)"""
import json
import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("ECHOS_DRY_RUN", "1")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from echos.config import load_config                                   # noqa: E402
from echos.team.planner import detector_scores, nearest                # noqa: E402
from echos.team import routing_budget as RB                            # noqa: E402
from scripts.evaluation import routing_eval as RE  # noqa: E402

BUDGETS = (0.1, 0.25, 0.5, 1.0)
DEFAULT_BUDGET = 0.25
N_BOOT = 1000


def replay_days() -> list:
    """routing_eval.load() plus patient / day keys (case_state order, same as load())."""
    from sqlalchemy import select
    from echos.orchestrator import state as S
    days = RE.load()
    eng = S.get_engine(str(RE.DB))
    with eng.begin() as c:
        keys = [(r.patient_id, str(r.day)) for r in c.execute(select(S.case_state)).fetchall()]
    for d, (p, day) in zip(days, keys):
        d["patient_id"], d["day"] = p, day
    return days


def label_days() -> set:
    """(patient, day) with any clinical event label. Provenance of Labels.csv is unverified: used as a weak sensitivity check only."""
    from echos.data.loaders import load_labels
    lab = load_labels()
    return {(str(p), str(d)[:10]) for p, d in zip(lab["patient_id"], lab["day"])}


def build() -> dict:
    cfg, t = load_config(), load_config()["triage"]
    days = replay_days()
    n = len(days)
    patient = np.array([d["patient_id"] for d in days])
    normal = np.array([d["decision"]["status"] == "NORMAL" for d in days])
    r = np.array([nearest(detector_scores(d["results"], t))[0] for d in days])
    labelled = label_days()
    is_lab = np.array([(d["patient_id"], d["day"][:10]) in labelled for d in days])
    pw = n / 7.0
    n_norm, n_pat = int(normal.sum()), int(len(np.unique(patient)))
    grid = {}
    for B in BUDGETS:
        th = RB.budget_theta(r[normal], B, n)
        called = normal & (r > th)
        calls = int(called.sum())
        grid[str(B)] = {"allowed_false_calls": RB.allowed_calls(B, n), "theta": th, "false_calls": calls, "false_calls_per_patient_week": round(calls / pw, 3),
                        "labelled_normal_days_called": int((called & is_lab).sum()),
                        "labelled_share_called": None if not calls else round(float((called & is_lab).sum() / calls), 3),
                        "labelled_share_not_called": round(float((normal & ~called & is_lab).sum() / max((normal & ~called).sum(), 1)), 3)}
    boot = RB.bootstrap_theta(r, patient, normal, DEFAULT_BUDGET, N_BOOT, cfg["seed"])
    d0 = grid[str(DEFAULT_BUDGET)]
    return {"method": "call budget (label-free): false calls on Normal days <= B per patient-week", "default_budget_per_patient_week": DEFAULT_BUDGET,
            "patient_days": n, "patients": n_pat, "patient_weeks": round(pw, 2), "normal_days": n_norm, "labelled_days_total": int(is_lab.sum()),
            "labelled_normal_days": int((normal & is_lab).sum()),
            "theta": d0["theta"], "false_calls_per_patient_week_achieved": d0["false_calls_per_patient_week"],
            "bootstrap_by_patient_95": boot, "budget_grid": grid, "r_normal_quantiles_50_90_99_max": [round(float(x), 3) for x in np.quantile(r[normal], [0.5, 0.9, 0.99, 1.0])],
            "notes": ["theta is a fraction of each detector's own alert threshold (r = p / tau, max over detectors); it is a quantile of r on the Normal days, so it depends on those days and on the detectors' score scale.",
                      f"patient-level bootstrap over {n_pat} patients: wide intervals mean few patients drive the tail of the r distribution",
                      "Labels.csv provenance is unverified; the label columns are a weak sensitivity check, not validation (no claim that near-miss days are more often clinically relevant).",
                      "r is a routing score, not a probability; nothing labels 'the right agents', so the budget is a stated preference (how many near-miss watch files per patient-week), not an estimate of harm."]}


if __name__ == "__main__":
    res = build()
    (ROOT / "reports/metrics/routing_thresholds.json").write_text(json.dumps(res, indent=1))
    print(f"days {res['patient_days']} (patients {res['patients']}, normal {res['normal_days']}); r on Normal days q50/90/99/max {res['r_normal_quantiles_50_90_99_max']}")
    for B, g in res["budget_grid"].items():
        print(f"B={B:>4}/pw  allowed {g['allowed_false_calls']:>3} | theta {g['theta']:.4f} calls {g['false_calls']} ({g['false_calls_per_patient_week']}/pw) labelled {g['labelled_normal_days_called']}/{g['false_calls']}")
    print("default", res["default_budget_per_patient_week"], "theta", res["theta"], "bootstrap", res["bootstrap_by_patient_95"])
