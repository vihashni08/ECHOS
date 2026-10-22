"""Label-validity audit of TIHM -> reports/metrics/v2_label_audit.json

Questions:
 1. Do the BP / pulse labels simply follow a threshold on the same day's readings?
 2. How close in time is a label to the reading before / after it?
 3. When are agitation labels timestamped (batch times of the study's 6-hourly model)?
 4. Can a NEW vitals episode (label with no label or threshold crossing in the 7 days before) be predicted 1-3 days ahead?
Answers decide what ECHOS claims: same-day vitals detection would re-learn the threshold, so ECHOS ranks patients by their personal event rate
and does not claim to forecast new episodes.

    PYTHONPATH=. python scripts/evaluation/label_audit.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from echos.config import ROOT, load_config
from echos.data import loaders
from echos.data.splits import patient_folds
from echos.monitor import cohort, metrics as M

OUT = ROOT / "reports" / "metrics" / "v2_label_audit.json"


def threshold_table(d: pd.DataFrame) -> list[dict]:
    rows = []
    bp_lab = d["bp_label"].values
    for s, di in [(140, 90), (150, 95), (160, 100), (170, 105), (190, 110)]:
        c = ((d.sys >= s) | (d.dia >= di)).values
        rows.append({"rule": f"sys>={s} or dia>={di}", "days_crossing": int(c.sum()), "share_labelled": float(bp_lab[c].mean()) if c.any() else None,
                     "share_of_bp_labels_on_crossing_days": float(bp_lab[c].sum() / bp_lab.sum())})
    return rows


def main() -> dict:
    phys, labels = loaders.load_physiology(), loaders.load_labels()
    d = cohort.vitals_cohort(phys, labels)
    bp = labels[labels.type == "Blood pressure"][["patient_id", "day"]].drop_duplicates().assign(bp_label=1)
    d = d.merge(bp, on=["patient_id", "day"], how="left").fillna({"bp_label": 0})
    rd = d[d.has_reading == 1]
    out: dict = {"cohort": {"patients": int(d.patient_id.nunique()), "patient_days": int(len(d)), "reading_days": int(len(rd))}}
    out["q1_bp_labels_vs_thresholds"] = threshold_table(rd)
    lab_q = rd.groupby("bp_label")[["sys", "dia", "hr"]].median()
    out["q1_median_readings"] = {"unlabelled_days": lab_q.loc[0].round(1).to_dict(), "bp_labelled_days": lab_q.loc[1].round(1).to_dict()}

    # 2. timing of BP labels relative to the nearest systolic readings
    sbp = phys[phys.device_type == "Systolic blood pressure"][["patient_id", "date"]].sort_values("date").rename(columns={"date": "rdate"})
    L = labels[labels.type == "Blood pressure"].sort_values("date")
    back = pd.merge_asof(L, sbp, left_on="date", right_on="rdate", by="patient_id", direction="backward")
    fwd = pd.merge_asof(L, sbp, left_on="date", right_on="rdate", by="patient_id", direction="forward")
    hb = (back.date - back.rdate).dt.total_seconds() / 3600
    hf = (fwd.rdate - fwd.date).dt.total_seconds() / 3600
    out["q2_bp_label_timing_hours"] = {"since_previous_reading_median": float(hb.median()), "to_next_reading_median": float(hf.median()),
                                      "share_with_a_reading_within_1h": float(((hb <= 1) | (hf <= 1)).mean())}

    # 3. agitation timestamps
    ag = labels[labels.type == "Agitation"]
    out["q3_agitation_label_hours"] = {str(k): int(v) for k, v in ag.date.dt.hour.value_counts().sort_index().items()}
    out["q3_agitation"] = {"labels": int(len(ag)), "patients": int(ag.patient_id.nunique()), "distinct_dates": int(ag.date.dt.date.nunique()),
                           "max_labels_one_patient": int(ag.groupby("patient_id").size().max())}

    # 4. new-episode onset predictability
    s = load_config()["monitor"]["attention"]
    d = d.sort_values(["patient_id", "day"]).reset_index(drop=True)
    d["cross"] = ((d.sys > s["sys_high"]) | (d.dia > s["dia_high"]) | (d.hr > s["hr_high"]) | (d.hr < s["hr_low"]) | (d.sys >= 160) | (d.dia >= 100)).astype(int)
    g = d.groupby("patient_id")
    d["flag"] = ((d.event == 1) | (d.cross == 1)).astype(int)
    quiet7 = g["flag"].transform(lambda x: x.shift(1).rolling(7, min_periods=7).sum()) == 0
    d["onset"] = ((d.event == 1) & quiet7).astype(int)
    d["y"] = sum(g["onset"].shift(-k).fillna(0) for k in (1, 2, 3)).clip(upper=1)
    quiet_now = g["flag"].transform(lambda x: x.rolling(7, min_periods=1).sum()) == 0
    q = d[quiet_now].copy()
    for c in ("sys", "dia", "hr"):
        q[f"{c}_max7"] = g[c].transform(lambda x: x.rolling(7, min_periods=1).max())[quiet_now]
        q[f"{c}_mean7"] = g[c].transform(lambda x: x.rolling(7, min_periods=1).mean())[quiet_now]
    q["label_rate"] = g["event"].transform(lambda x: x.shift(1).expanding().mean())[quiet_now].fillna(0)
    feats = [c for c in q.columns if c.endswith(("_max7", "_mean7"))] + ["label_rate"]
    oof = np.zeros(len(q))
    for tr, te in patient_folds(q, 5, load_config()["seed"]):
        m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=4000, C=0.3)).fit(q.iloc[tr][feats].fillna(0), q.iloc[tr]["y"])
        oof[te] = m.predict_proba(q.iloc[te][feats].fillna(0))[:, 1]
    yq = q["y"].values
    out["q4_new_episode_onsets"] = {"onsets": int(d.onset.sum()), "quiet_rows": int(len(q)), "positives": int(yq.sum()), "prevalence": float(yq.mean()),
                                    "logistic_pr_auc": float(M.summary(yq, oof, 7, prob=False)["pr_auc"]),
                                    "label_rate_pr_auc": float(M.summary(yq, q["label_rate"].values, 7, prob=False)["pr_auc"])}
    # 5. the URGENT rule (red flags) vs TIHM's attention limits: how often each fires and how often the study confirmed an event that day
    rd2 = d[d.has_reading == 1]
    u = np.array([bool(cohort.urgent_flags(r)) for r in rd2[["sys", "dia", "hr", "temp"]].to_dict("records")])
    at = np.array([bool(cohort.safety_flags(r)) for r in rd2[["sys", "dia", "hr", "temp", "water"]].to_dict("records")])
    pw = len(d) / 7
    out["q5_alert_rules"] = {"urgent_red_flags": {"share_of_reading_days": float(u.mean()), "per_patient_week": float(u.sum() / pw),
                                                  "share_confirmed_by_study": float(rd2.event.values[u].mean()) if u.any() else None},
                             "tihm_attention_limits": {"share_of_reading_days": float(at.mean()), "per_patient_week": float(at.sum() / pw),
                                                       "share_confirmed_by_study": float(rd2.event.values[at].mean()) if at.any() else None}}
    out["conclusions"] = [
        "BP labels concentrate on days with sys>=160 or dia>=100: same-day detection would re-learn a threshold.",
        "Labels sit close in time to a reading: the study's daily check produced them.",
        "Agitation labels carry only the batch times of the study's ~6-hourly model, so ECHOS scores the same windows.",
        "New vitals episodes are close to unpredictable from daily vitals; ECHOS does not claim to forecast them.",
    ]
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
