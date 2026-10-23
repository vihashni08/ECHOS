"""Data behind the interactive demo screens ('Test it yourself'). Everything is computed from real TIHM data and the out-of-fold scores.

  reports/metrics/v2_demo_audit.json   AGGREGATE grid: patient-days by (systolic, diastolic) bin, and how many carry a BP label (the 160/100 'wall')
  reports/metrics/v2_demo_budget.json  AGGREGATE curves: events per review for K = 1..10 and fairness floor off / 3 / 7 / 14 days, plus baselines
  data/v2/game.json                    PATIENT-LEVEL (git-ignored): days for 'You vs ECHOS' - what a clinician could see, what ECHOS scored, what happened

    PYTHONPATH=. python scripts/evaluation/demo_data.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from echos.config import ROOT
from echos.monitor import agitation as A, allocation as AL, cohort
from scripts.evaluation.allocation_eval import load, run

OUT = ROOT / "reports" / "metrics"
V2 = ROOT / "data" / "v2"
K_GAME, DAYS_GAME = 4, 10


def audit_grid() -> dict:
    d = cohort.vitals_cohort()
    d = d[d.has_reading == 1].copy()
    lab = pd.read_csv(ROOT / "data" / "tihm" / "Dataset" / "Labels.csv")
    lab["day"] = pd.to_datetime(lab["date"]).dt.normalize()
    bp = lab[lab["type"] == "Blood pressure"][["patient_id", "day"]].drop_duplicates().assign(bp_label=1)
    d = d.merge(bp, on=["patient_id", "day"], how="left").fillna({"bp_label": 0})
    d["sb"] = (d["sys"] // 5 * 5).clip(90, 220)
    d["db"] = (d["dia"] // 5 * 5).clip(40, 130)
    g = d.groupby(["sb", "db"]).agg(n=("bp_label", "size"), labelled=("bp_label", "sum")).reset_index()
    over = (d.sys >= 160) | (d.dia >= 100)
    return {"note": "patient-days with a BP reading, binned by the day's maximum systolic / diastolic (5 mmHg bins); labelled = a validated BP label that day",
            "cells": [{"sys": int(r.sb), "dia": int(r.db), "n": int(r.n), "labelled": int(r.labelled)} for r in g.itertuples()],
            "wall": {"rule": "systolic >= 160 or diastolic >= 100", "days": int(over.sum()), "share_labelled": float(d[over].bp_label.mean()),
                     "below_days": int((~over).sum()), "below_share_labelled": float(d[~over].bp_label.mean())}}


def budget_curves() -> dict:
    d = load()
    out = {"k": list(range(1, 11)), "policies": {}}
    pols = {"ECHOS": ("echos", None), "ECHOS + fairness 3 d": ("echos", 3), "ECHOS + fairness 7 d": ("echos", 7), "ECHOS + fairness 14 d": ("echos", 14),
            "Review past TIHM BP limits": ("tihm_limits", None), "Vitals only": ("vitals_only", None), "Agitation only": ("agitation_only", None), "Round robin": ("round_robin", None), "Random": ("random", None)}
    for name, (pol, fair) in pols.items():
        rows = []
        for k in out["k"]:
            if pol == "random":
                rs = [run(d, pol, k, fair, seed=s) for s in range(5)]
                r = {key: float(np.mean([x[key] for x in rs])) for key in rs[0]}
            else:
                r = run(d, pol, k, fair)
            rows.append({key: round(float(r[key]), 4) for key in ("events_per_review", "agitation_followed_up", "vitals_anticipated", "patients_never_reviewed", "max_days_without_review")})
        out["policies"][name] = rows
    return out


def game() -> dict:
    d = load().rename(columns={"v": "v", "a": "a"})
    vit = pd.read_parquet(V2 / "oof_vitals.parquet")[["patient_id", "day", "sys_z", "dia_z", "hr_z"]]
    ev14 = pd.read_parquet(V2 / "oof_vitals.parquet")[["patient_id", "day", "event"]].sort_values(["patient_id", "day"])
    ev14["events_14d"] = ev14.groupby("patient_id")["event"].transform(lambda s: s.shift(1).rolling(14, min_periods=1).sum()).fillna(0)
    act = A.build_table()
    act["day"] = (act["w"] - pd.Timedelta(hours=1)).dt.normalize()
    actd = act.groupby(["patient_id", "day"]).agg(activity=("total", "sum"), activity_z=("total_z", "max")).reset_index()
    d = d.merge(vit, on=["patient_id", "day"], how="left").merge(ev14[["patient_id", "day", "events_14d"]], on=["patient_id", "day"], how="left").merge(actd, on=["patient_id", "day"], how="left")
    d["events_14d"] = d["events_14d"].fillna(0)
    v_bar, a_bar = float(d["v"].mean()), float(d["a"].mean())
    ev = {(r.patient_id, r.day) for r in d[d.event == 1].itertuples()}
    d["future"] = [sum(((r.patient_id, r.day + pd.Timedelta(days=h)) in ev) for h in (1, 2, 3)) for r in d.itertuples()]
    d["found"] = d["ep"] + d["future"]
    d["lift"] = [AL.priority(r.v, r.a, v_bar, a_bar) for r in d.itertuples()]
    d["tihm_flag"] = [bool(cohort.safety_flags({"sys": r.sys, "dia": r.dia, "hr": r.hr})) for r in d.itertuples()]
    days = d.groupby("day").agg(n=("patient_id", "size"), found=("found", "sum")).reset_index()
    days = days[days.n >= 25]                        # chosen by caseload size only, never by outcome
    last = d.day.max() - pd.Timedelta(days=3)
    days = days[days.day <= last]
    pick = days.iloc[np.linspace(0, len(days) - 1, DAYS_GAME).round().astype(int)]
    rng = np.random.default_rng(0)
    out = []
    for day in pick.day:
        g = d[d.day == day].reset_index(drop=True)
        names = {p: f"P{i + 1:02d}" for i, p in enumerate(sorted(g.patient_id))}
        echos = list(g.sort_values(["lift", "patient_id"], ascending=[False, True]).head(K_GAME).patient_id)
        thr = list(g.assign(s=g.tihm_flag.astype(int) * 1000 + g.sys.fillna(0)).sort_values("s", ascending=False).head(K_GAME).patient_id)
        tot = g.set_index("patient_id")["found"]
        ag, vi = g.set_index("patient_id")["ep"], g.set_index("patient_id")["future"]
        rand = float(np.mean([tot.loc[list(rng.choice(g.patient_id, K_GAME, replace=False))].sum() for _ in range(500)]))
        r = lambda x: None if x is None or x != x else round(float(x), 1)  # noqa: E731
        out.append({"day": day.strftime("%Y-%m-%d"), "k": K_GAME,
                    "patients": [{"id": names[x.patient_id], "real_id": x.patient_id, "sys": r(x.sys), "dia": r(x.dia), "hr": r(x.hr), "sys_z": r(x.sys_z),
                                  "events_14d": int(x.events_14d or 0), "activity": r(x.activity), "activity_z": r(x.activity_z), "tihm_flag": bool(x.tihm_flag),
                                  "agitation_episodes": int(x.ep), "vitals_events_next3": int(x.future), "found": int(x.found), "lift": round(float(x.lift), 2)}
                                 for x in g.itertuples()],
                    "echos": [names[p] for p in echos], "threshold": [names[p] for p in thr],
                    "echos_found": int(tot.loc[echos].sum()), "threshold_found": int(tot.loc[thr].sum()),
                    "echos_split": {"agitation": int(ag.loc[echos].sum()), "vitals": int(vi.loc[echos].sum())},
                    "threshold_split": {"agitation": int(ag.loc[thr].sum()), "vitals": int(vi.loc[thr].sum())}, "random_found": round(rand, 2),
                    "best_possible": int(g.found.sort_values(ascending=False).head(K_GAME).sum())})
    return {"k": K_GAME, "found_definition": "agitation episodes in that day's windows + confirmed BP/pulse event days in the next 3 days, among the people picked",
            "days": out}


def main() -> None:
    (OUT / "v2_demo_audit.json").write_text(json.dumps(audit_grid()))
    (OUT / "v2_demo_budget.json").write_text(json.dumps(budget_curves(), indent=1))
    g = game()
    (V2 / "game.json").write_text(json.dumps(g))
    s = lambda k: sum(x[k] for x in g["days"])  # noqa: E731
    print(f"game: {len(g['days'])} days, found by ECHOS {s('echos_found')}, threshold {s('threshold_found')}, random {s('random_found'):.1f}, best possible {s('best_possible')}")


if __name__ == "__main__":
    main()
