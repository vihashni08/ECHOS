"""System ablation on OUT-OF-FOLD scores (models/oof_scores.parquet: patient-level folds, calibrated nested OOF) with patient-level bootstrap 95% CIs.
  A  fixed thresholds: BP rule (config agent1.fixed_rule) for vitals, simple night-activity rule (night_events_z >= 2 vs the patient's own PAST 28-day
     baseline, shift(1)) for agitation. Binary rules: they have one alert budget, B and C are matched to it.
  B  vitals sub-model only.
  C  full system = vitals + behaviour sub-models + the arbiter (rule-based triage): vitals path = vitals score (+ absolute critical-vitals limits forced in),
     agitation path = behaviour score.
Tables: (1) vitals-labelled days, (2) agitation-labelled days (both at matched budgets), (3) day-level "any labelled alert" at the shipped thresholds.
Budgets are alerts per patient-week of the evaluated population (all patient-days / 7); false alerts = alert days without the target label.
Writes reports/metrics/ablation.json and the block <!-- ablation:start/end --> in reports/RESULTS.md. No model is trained here.
Usage: PYTHONPATH=. python scripts/ablation.py"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
from echos.agents.health import behaviour as b  # noqa: E402
from echos.config import ROOT, load_config  # noqa: E402
from echos.data.patient_day import load_patient_day  # noqa: E402

RESULTS = ROOT / "reports/RESULTS.md"
OUT = ROOT / "reports/metrics/ablation.json"
START, END = "<!-- ablation:start -->", "<!-- ablation:end -->"
NB, BUDGETS = 1000, (0.5, 1.0, 2.0)


def topk(score: np.ndarray, k: int, mask: np.ndarray, force: np.ndarray | None = None) -> np.ndarray:
    """Boolean alert vector: the k highest scores among `mask` days (days in `force` always alert and count towards k)."""
    a = np.zeros(len(score), bool)
    if force is not None:
        a |= force & mask
    rest = k - int(a.sum())
    if rest > 0:
        s = np.where(mask & ~a, score, -np.inf)
        idx = np.argsort(-s, kind="stable")[:rest]
        a[idx[np.isfinite(s[idx])]] = True
    return a


def load() -> pd.DataFrame:
    cfg = load_config()
    pdy = load_patient_day()
    pdy["day"] = pd.to_datetime(pdy["day"])
    oof = pd.read_parquet(ROOT / "models/oof_scores.parquet")
    v = oof[oof.agent == "physiological"].rename(columns={"oof": "v_oof"})[["patient_id", "day", "v_oof"]]
    a = oof[oof.agent == "behaviour"].rename(columns={"oof": "a_oof"})[["patient_id", "day", "a_oof"]]
    bf = b.build_features(pdy)
    bf["day"] = pd.to_datetime(bf["day"])
    bf = bf[["patient_id", "day", "night_events_z"]]
    d = pdy.merge(v, on=["patient_id", "day"], how="left").merge(a, on=["patient_id", "day"], how="left").merge(bf, on=["patient_id", "day"], how="left")
    r, cv = cfg["agent1"]["fixed_rule"], cfg["triage"]["critical_vitals"]
    d["has_v"] = d.v_oof.notna()
    d["has_a"] = d.a_oof.notna()
    d["y_v"] = ((d.bp_alert + d.pulse_alert + d.water_alert) > 0)
    d["y_a"] = d.agitation > 0
    d["rule_bp"] = d.has_v & ((d.sys >= r["sys_high"]) | (d.dia >= r["dia_high"]) | (d.sys < r["sys_low"]))
    d["rule_night"] = d.has_a & (d.night_events_z >= 2.0)
    d["crit"] = d.has_v & ((d.sys >= cv["sys_high"]) | (d.sys < cv["sys_low"]) | (d.dia >= cv["dia_high"]) | (d.hr >= cv["hr_high"]) | (d.hr < cv["hr_low"]))
    return d.sort_values(["patient_id", "day"]).reset_index(drop=True)


def pctl(x):
    return [float(np.percentile(x, 2.5)), float(np.percentile(x, 97.5))]


def evaluate(d: pd.DataFrame, systems: dict, label: str, pop: str, rng) -> dict:
    """systems: name -> fn(sub_df, pw) -> bool alert vector. Metrics on `label` positives within days of population `pop`."""
    pids = d.patient_id.unique()
    groups = {p: np.where(d.patient_id.values == p)[0] for p in pids}
    boots = [np.concatenate([groups[p] for p in rng.choice(pids, len(pids))]) for _ in range(NB)]

    def metrics(sub: pd.DataFrame, fn) -> tuple:
        pw = len(sub) / 7.0
        al = fn(sub, pw)
        y = sub[label].values & sub[pop].values
        tp = int((al & y).sum())
        return tp / max(int(y.sum()), 1), (int(al.sum()) - tp) / pw, int(al.sum()) / pw, int(al.sum())

    out = {}
    for name, fn in systems.items():
        pt = metrics(d, fn)
        bs = np.array([metrics(d.iloc[i], fn)[:3] for i in boots])
        out[name] = {"recall": pt[0], "recall_ci95": pctl(bs[:, 0]), "false_alerts_per_pw": pt[1], "false_alerts_per_pw_ci95": pctl(bs[:, 1]),
                     "alerts_per_pw": pt[2], "alert_days": pt[3], "chance_recall": pt[3] / max(int(d[pop].sum()), 1), "positives": int((d[label] & d[pop]).sum())}
    return out


def fmt(r):
    return f"{r['recall']:.2f} ({r['recall_ci95'][0]:.2f}-{r['recall_ci95'][1]:.2f})", f"{r['false_alerts_per_pw']:.2f} ({r['false_alerts_per_pw_ci95'][0]:.2f}-{r['false_alerts_per_pw_ci95'][1]:.2f})"


def main():
    cfg = load_config()
    T = cfg["triage"]
    d = load()
    rng = np.random.RandomState(cfg["seed"])
    res = {"patient_days": len(d), "patient_weeks": round(len(d) / 7, 1), "patients": int(d.patient_id.nunique()),
           "vitals_days_scored": int(d.has_v.sum()), "vitals_positive_days": int((d.y_v & d.has_v).sum()),
           "behaviour_days_scored": int(d.has_a.sum()), "agitation_positive_days": int((d.y_a & d.has_a).sum()),
           "agitation_patients": int(d[d.y_a & d.has_a].patient_id.nunique()), "tables": {}}
    # ---- (1) vitals target
    def mk_v(kind):
        def f(s, pw, rate=None):
            k = int(round(s.rule_bp.sum())) if rate is None else int(round(rate * pw))
            force = s.crit.values if kind == "C" else None
            return topk(s.v_oof.fillna(-1).values, max(k, 1), s.has_v.values, force)
        return f
    t1 = {}
    for lab, rate in [("A's budget", None)] + [(f"{r} / pt-wk", r) for r in BUDGETS]:
        sy = {"B vitals sub-model only": (lambda s, pw, r=rate: mk_v("B")(s, pw, r)), "C full system": (lambda s, pw, r=rate: mk_v("C")(s, pw, r))}
        if rate is None:
            sy = {"A fixed BP rule": lambda s, pw: s.rule_bp.values, **sy}
        t1[lab] = evaluate(d, sy, "y_v", "has_v", rng)
    res["tables"]["vitals_labelled_days"] = t1
    # ---- (2) agitation target
    def mk_a(kind):
        def f(s, pw, rate=None):
            k = int(round(s.rule_night.sum())) if rate is None else int(round(rate * pw))
            sc = s.a_oof if kind == "C" else s.v_oof
            m = s.has_a.values if kind == "C" else s.has_v.values
            return topk(sc.fillna(-1).values, max(k, 1), m)
        return f
    t2 = {}
    for lab, rate in [("A's budget", None)] + [(f"{r} / pt-wk", r) for r in BUDGETS]:
        sy = {"B vitals sub-model only": (lambda s, pw, r=rate: mk_a("B")(s, pw, r)), "C full system (behaviour path)": (lambda s, pw, r=rate: mk_a("C")(s, pw, r))}
        if rate is None:
            sy = {"A night-activity rule": lambda s, pw: s.rule_night.values, **sy}
        t2[lab] = evaluate(d, sy, "y_a", "has_a", rng)
    res["tables"]["agitation_labelled_days"] = t2
    # ---- (3) any labelled alert, shipped thresholds
    d["y_any"] = (d.y_v & d.has_v) | (d.y_a & d.has_a)
    d["all_days"] = True
    c_ship = lambda s, pw: ((s.v_oof.fillna(-1) >= T["vitals_prob"]) | (s.a_oof.fillna(-1) >= T["agitation_prob"]) | s.crit).values   # noqa: E731
    b_ship = lambda s, pw: ((s.v_oof.fillna(-1) >= T["vitals_prob"]) | s.crit).values   # noqa: E731
    n_c = int(c_ship(d, 0).sum())
    sy3 = {"A fixed rules (BP rule + night rule)": lambda s, pw: (s.rule_bp | s.rule_night).values,
           "B vitals only, shipped threshold": b_ship,
           "B vitals only, matched to C's budget": lambda s, pw: topk(s.v_oof.fillna(-1).values, int(round(c_ship(s, pw).sum())), s.has_v.values),
           "C full system, shipped thresholds": c_ship}
    res["tables"]["any_labelled_day"] = evaluate(d, sy3, "y_any", "all_days", rng)
    res["any_label_positives"] = int(d.y_any.sum())
    res["shipped_thresholds"] = {"vitals_prob": T["vitals_prob"], "agitation_prob": T["agitation_prob"]}
    res["notes"] = ["OOF scores only; labels are TIHM events described as clinician-verified (production not verified by us; treat vitals results as an upper bound); recall on labelled days of the scored population",
                    "budgets = alert days per patient-week over ALL patient-days / 7; 'A budget' = number of days the fixed rule fires",
                    "patient-level bootstrap (1000 resamples); budgets are recomputed inside each resample",
                    "C's vitals path is B plus the absolute critical-vitals limits, so C = B on vitals-labelled days up to those forced days",
                    "B on agitation days is the vitals score used as an agitation detector (a control: it has no behaviour input)",
                    "thresholds of the shipped system were chosen on these same OOF scores (mild optimism)"]
    OUT.write_text(json.dumps(res, indent=1))
    md = [START, "## System ablation (written by scripts/ablation.py)", "",
          f"Out-of-fold scores, patient-level folds; patient-level bootstrap 95% CIs (1000 resamples). {res['patients']} patients, {res['patient_days']} patient-days "
          f"({res['patient_weeks']} patient-weeks); {res['vitals_positive_days']} vitals-labelled and {res['agitation_positive_days']} agitation-labelled days "
          f"({res['agitation_patients']} patients). A = fixed thresholds, B = vitals sub-model only, C = full system (vitals + behaviour + arbiter). "
          "Budget = alert days per patient-week; false alerts = alert days without the target label.", ""]
    for key, title in (("vitals_labelled_days", "Vitals-labelled days (BP / pulse / body-water clinical alerts)"),
                       ("agitation_labelled_days", "Agitation-labelled days")):
        md += [f"### {title}", "", "| Budget | System | Alerts / pt-wk | Recall (95% CI) | Chance recall | False alerts / pt-wk (95% CI) |", "|---|---|---|---|---|---|"]
        for bud, sy in res["tables"][key].items():
            for name, r in sy.items():
                rc, fa = fmt(r)
                md.append(f"| {bud} | {name} | {r['alerts_per_pw']:.2f} | {rc} | {r['chance_recall']:.2f} | {fa} |")
        md.append("")
    md += ["### Day-level: any labelled clinical alert (vitals or agitation), shipped thresholds", "",
           f"Positives: {res['any_label_positives']} labelled days. Shipped thresholds: vitals {T['vitals_prob']}, agitation {T['agitation_prob']} (+ critical limits).", "",
           "| System | Alerts / pt-wk | Recall (95% CI) | False alerts / pt-wk (95% CI) |", "|---|---|---|---|"]
    for name, r in res["tables"]["any_labelled_day"].items():
        rc, fa = fmt(r)
        md.append(f"| {name} | {r['alerts_per_pw']:.2f} | {rc} | {fa} |")
    md += ["", "VERDICT_PLACEHOLDER", END]
    p = RESULTS.read_text()
    block = "\n".join(md)
    res_text = block
    # verdicts computed from the numbers (written by this script, never typed)
    verd = []
    for key, base, full in (("vitals_labelled_days", "B vitals sub-model only", "C full system"), ("agitation_labelled_days", "B vitals sub-model only", "C full system (behaviour path)")):
        for bud, sy in res["tables"][key].items():
            rb, rc_ = sy[base], sy[full]
            better = rc_["recall"] > rb["recall"] and rc_["recall_ci95"][0] > rb["recall_ci95"][1]
            same = abs(rc_["recall"] - rb["recall"]) < 0.005
            verd.append(f"{key.split('_')[0]} @ {bud}: C {rc_['recall']:.2f} vs B {rb['recall']:.2f} -> "
                        + ("C clearly better (CIs do not overlap)" if better else "no difference" if same else "C higher, CIs overlap" if rc_["recall"] > rb["recall"] else "C NOT better than B"))
    res_text = res_text.replace("VERDICT_PLACEHOLDER", "**C vs B (recall at matched budget):** " + "; ".join(verd) + ".")
    p = p[:p.index(START)] + res_text + p[p.index(END) + len(END):] if START in p else p.rstrip() + "\n\n" + res_text + "\n"
    RESULTS.write_text(p)
    res["verdicts"] = verd
    OUT.write_text(json.dumps(res, indent=1))
    for key in ("vitals_labelled_days", "agitation_labelled_days"):
        print(key)
        for bud, sy in res["tables"][key].items():
            for name, r in sy.items():
                rc, fa = fmt(r)
                print(f"  {bud:<14} {name:<46} {r['alerts_per_pw']:.2f}/wk recall {rc} FA {fa}")
    print("any")
    for name, r in res["tables"]["any_labelled_day"].items():
        rc, fa = fmt(r)
        print(f"  {name:<46} {r['alerts_per_pw']:.2f}/wk recall {rc} FA {fa}")
    print("; ".join(verd))


if __name__ == "__main__":
    main()
