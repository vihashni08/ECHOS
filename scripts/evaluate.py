"""Merge reports/metrics/*.json into one results table (reports/results_table.md + reports/results_table.csv).

Usage: PYTHONPATH=. python3 scripts/evaluate.py     (re-run the agents' own evaluate() first to refresh the JSONs)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

M = Path("reports/metrics")
rows: list = []  # section, method, metric, value, lo, hi, note


def add(section, method, metric, v, ci=None, note=""):
    rows.append(dict(section=section, method=method, metric=metric, value=v,
                     ci_low=ci[0] if ci else None, ci_high=ci[1] if ci else None, note=note))


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [c - h, c + h]


def f(x, ci=None, d=3):
    s = "n/a" if x is None else f"{x:.{d}f}"
    return s if not ci else f"{s} ({ci[0]:.{d}f}-{ci[1]:.{d}f})"


def load(n):
    p = M / f"{n}.json"
    return json.loads(p.read_text()) if p.exists() else None


def table(headers, body):
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"] +
                     ["| " + " | ".join(str(c) for c in r) + " |" for r in body])


def build() -> str:
    out = []
    a1 = load("agent1")
    if a1:
        r, x, ci = a1["results"], a1["results"]["xgboost"], a1["bootstrap_95ci"]
        body = [["Fixed rule (sys>=140 / dia>=90 / sys<100)", f(r["fixed_rule"]["pr_auc"], ci["rule_pr_auc"]),
                 f(r["fixed_rule"]["recall"], ci["rule_recall"]), f(r["fixed_rule"]["false_alerts_per_patient_week"], d=2), r["fixed_rule"]["alerts"]]]
        for name, k, cik in [("XGBoost @ same alert count as rule", "at_rule_budget", "xgb_recall@rule_budget"),
                             ("XGBoost @ 0.5 alerts/pt-wk", "at_0.5_alerts_per_patient_week", "xgb_recall@0.5"),
                             ("XGBoost @ 1.0 alerts/pt-wk", "at_1.0_alerts_per_patient_week", "xgb_recall@1.0"),
                             ("XGBoost @ 2.0 alerts/pt-wk", "at_2.0_alerts_per_patient_week", "xgb_recall@2.0")]:
            s = x[k]
            body.append([name, f(x["pr_auc"], ci["xgb_pr_auc"]), f(s["recall"], ci[cik]), f(s["false_alerts_per_patient_week"], d=2), s["alerts"]])
            add("agent1", name, "recall", s["recall"], ci[cik])
            add("agent1", name, "false_alerts_per_patient_week", s["false_alerts_per_patient_week"])
        add("agent1", "fixed rule", "pr_auc", r["fixed_rule"]["pr_auc"], ci["rule_pr_auc"])
        add("agent1", "fixed rule", "recall", r["fixed_rule"]["recall"], ci["rule_recall"])
        add("agent1", "fixed rule", "false_alerts_per_patient_week", r["fixed_rule"]["false_alerts_per_patient_week"])
        add("agent1", "XGBoost", "pr_auc", x["pr_auc"], ci["xgb_pr_auc"])
        add("agent1", "prevalence (no-skill PR-AUC)", "pr_auc", a1["prevalence"], ci["prevalence"])
        out += [f"### Agent 1 (vitals), out-of-fold, patient-level GroupKFold. {a1['n_reading_days']} reading-days, {a1['n_patients']} patients, "
                f"prevalence {a1['prevalence']:.3f}", table(["Method", "PR-AUC (95% CI)", "Recall (95% CI)", "False alerts / patient-week", "Alerts"], body),
                "SHAP top features: " + ", ".join(f"{k} {v:.2f}" for k, v in a1["shap_global_top5"].items())]
    a2 = load("agent2")
    if a2:
        p, ci = a2["point"], a2["bootstrap_95ci"]
        body = [["PR-AUC (full agent)", f(p["pr_auc"], ci["pr_auc"]), f"{p['pr_auc'] / p['prevalence']:.1f}x base rate"],
                ["PR-AUC, base rate (no skill)", f(p["prevalence"], ci["prevalence"]), "1.0x"],
                ["PR-AUC, personal-baseline drift only", f(p["drift_only_pr_auc"], ci["drift_only_pr_auc"]), f"{p['drift_only_pr_auc'] / p['prevalence']:.1f}x"],
                ["ROC-AUC (full agent)", f(p["roc_auc"], ci["roc_auc"]), "chance = 0.5"],
                ["ROC-AUC, drift only", f(p["drift_only_roc_auc"], ci["drift_only_roc_auc"]), "chance = 0.5"]]
        for b in ("0.5", "1.0", "2.0"):
            t = a2["alert_budget_table"][f"at_{b}_alerts_per_patient_week"]
            body.append([f"Recall @ {b} alerts/pt-wk", f(t["recall"], ci[f"recall@{b}"]), f"{t['false_alerts_per_patient_week']:.2f} false alerts/pt-wk"])
            add("agent2", f"@{b}/wk", "recall", t["recall"], ci[f"recall@{b}"])
            add("agent2", f"@{b}/wk", "false_alerts_per_patient_week", t["false_alerts_per_patient_week"])
        for k in ("pr_auc", "roc_auc", "drift_only_pr_auc", "drift_only_roc_auc", "prevalence"):
            add("agent2", k, k, p[k], ci[k])
        out += [f"### Agent 2 (behaviour / agitation) vs base rate, out-of-fold; CI = patient bootstrap. {a2['n_days']} days, "
                f"{a2['n_patients']} patients ({a2['n_patients_with_agitation']} with an agitation day)",
                table(["Metric", "Value (95% CI)", "Reference"], body), "Ablation (out-of-fold, from scripts/audit_agent2.py): " + _ablation(a2)]
        au = a2.get("within_patient_audit")
        if au:
            g = lambda t: f"{t[0]:.3f} (IQR {t[1]:.3f}-{t[2]:.3f})"
            wrows = [[n, f"{au[k]['pooled_roc_auc']:.3f}", g(au[k]["within_patient"]["roc_auc_median_q1_q3"]),
                     f"{au[k]['pooled_pr_auc']:.3f}", g(au[k]["within_patient"]["pr_auc_median_q1_q3"])]
                    for k, n in [("full", "Full feature set"), ("z_only", "Per-patient z-scores only (no absolute levels)"),
                                 ("drift_score_only", "Drift score only")]]
            w = au["full"]["within_patient"]
            out += [f"**Agent 2 headline (within-patient, {w['n_patients']} patients with both classes; median across patients):** "
                    f"ROC-AUC {g(w['roc_auc_median_q1_q3'])}, PR-AUC {g(w['pr_auc_median_q1_q3'])} "
                    f"(median per-patient prevalence {w['patient_prevalence_median']:.3f}). Pooled numbers above mix between-patient differences in activity level with within-patient signal.",
                    table(["Model", "Pooled ROC-AUC", "Within-patient ROC-AUC", "Pooled PR-AUC", "Within-patient PR-AUC"], wrows),
                    "Leakage audit: every baseline feature (z, CUSUM, drift, deltas) uses shift(1) past-only windows; raw same-day counts are the current observation, as is the same-day label. "
                    "Top gain importance: " + ", ".join(f"{k} {v:.2f}" for k, v in list(au["top10_gain_importance"].items())[:10]) + ". " + _drop_sentence(au)]
    a3 = load("agent3")
    if a3:
        body = []
        for g, v in a3["groups"].items():
            ci = wilson(round(v["sensitivity"] * v["n_falls"]), v["n_falls"])
            body.append([g, len(v["fall_subjects"]), v["n_falls"], f(v["sensitivity"], ci), f(v["specificity_windows"], d=4), f(v["false_alarms_per_hour"], d=2)])
            add("agent3", g, "sensitivity", v["sensitivity"], ci)
            add("agent3", g, "specificity", v["specificity_windows"])
            add("agent3", g, "false_alarms_per_hour", v["false_alarms_per_hour"])
        out += [f"### Agent 3 (falls, SisFall), subject-wise out-of-fold, threshold {a3['threshold']}. Overall PR-AUC {a3['pr_auc']:.3f}, "
                f"{a3['n_subjects']} subjects",
                table(["Group", "Fall subjects", "Fall trials", "Sensitivity (Wilson 95% CI)", "Specificity (windows)", "False alarms / hour"], body),
                "Wilson CIs treat trials as independent; trials are clustered within subjects, so true uncertainty is wider (elderly = one subject)."]
    e = load("e2e")
    if e:
        body = [["Patients / patient-days / patient-weeks", f"{e['patients']} / {e['patient_days']} / {e['patient_weeks']}"],
                ["Injected falls", e["injected_falls"]],
                ["Fall detection rate (URGENT, fall reason)", f(e["fall_detection_rate"], e["falls_detected_pct_ci95_wilson"]) + f" ({e['falls_detected_as_urgent']}/{e['injected_falls']})"],
                ["Detection delay, simulated (fall -> end of day), median / max", f"{e['detection_delay_sim_hours_median']} h / {e['detection_delay_sim_hours_max']} h"],
                ["Pipeline latency after last agent output, median", f"{e['pipeline_latency_s_median_after_last_agent_output']} s"],
                ["False fall alerts per patient-week", f"{e['false_fall_alerts_per_patient_week']} ({e['false_fall_alerts']} alerts)"],
                ["URGENT days from critical vitals (real TIHM readings)", e["urgent_critical_vitals_days"]],
                ["All URGENT alerts per patient-week", e["urgent_alerts_per_patient_week"]],
                ["Days Normal / Monitor / Urgent", " / ".join(f"{e['status_share'].get(k, 0):.1%}" for k in ("NORMAL", "MONITOR", "URGENT"))],
                ["Days with a data gap", f"{e['data_gap_share']:.1%}"], ["Duplicate alerts", e["duplicate_alerts"]]]
        for r_ in body:
            add("e2e", r_[0], "value", None, None, str(r_[1]))
        out += ["### End-to-end replay (systems run, full period, injected falls)", table(["Measure", "Value"], body)]
        if e.get("monitor_days_by_reason"):
            nm = sum(e["monitor_days_by_reason"].values())
            out += [f"MONITOR days by reason ({nm} days)", table(["Reason", "Days", "Share of MONITOR days"],
                    [[k, v, f"{v / nm:.1%}"] for k, v in e["monitor_days_by_reason"].items()]),
                    "DATA_GAP causes on MONITOR days (a day can have several)", table(["Cause", "Days"], [[k, v] for k, v in e["data_gap_days_by_cause_monitor_days"].items()])]
    mt = load("monitor_thresholds")
    if mt:
        o, n = mt["old"], mt["new"]
        out += ["### MONITOR thresholds re-selected from out-of-fold predictions (alert budget)",
                f"Budgets: vitals <= 1 alert per patient-week; behaviour Monitor days <= 10% of patient-days; agitation_prob is the only behaviour trigger "
                f"(drift is report evidence only). Score scale: {mt.get('score_scale', 'raw')}; the previous column is the earlier run on the raw score scale.",
                table(["", "Previous run (raw scale)", "Current"],
                      [["vitals_prob", o["vitals_prob"], n["vitals_prob"]], ["agitation_prob", o["agitation_prob"], n["agitation_prob"]],
                       ["Vitals alerts / patient-week", f"{o['vitals_alerts_per_patient_week']:.2f}", f"{n['vitals_alerts_per_patient_week']:.2f}"],
                       ["Vitals recall (BP/pulse/water labels)", f"{o['vitals_recall']:.3f}", f"{n['vitals_recall']:.3f}"],
                       ["Behaviour Monitor days, share of patient-days", f"{o['behaviour_monitor_share_of_patient_days']:.1%}", f"{n['behaviour_monitor_share_of_patient_days']:.1%}"],
                       ["Agitation recall of behaviour Monitor", f"{o['behaviour_monitor_recall']:.3f}", f"{n['behaviour_monitor_recall']:.3f}"]])]
    fi = load("fault_injection")
    if fi:
        body = [[r["scenario"], r["lost_alerts"], r["extra_alerts"], r["duplicate_alerts"], r["falls_alerted"],
                 f(r["detect_s"], d=1) if r["detect_s"] is not None else "-", f(r["recover_s"], d=1) if r["recover_s"] is not None else "-",
                 r["cases_identical_to_clean_run"], r.get("cases_differing_only_in_quality_flag", 0), r.get("cases_differing_other", 0),
                 f(r["passthrough_after_s"], d=1) if r.get("passthrough_after_s") is not None else "-",
                 r.get("agents12_outputs_without_quality", 0), ",".join(f"{k}:{v}" for k, v in r["report_modes"].items()), r["wall_overhead_s"]]
                for r in fi["rows"]]
        for r in fi["rows"]:
            add("fault", r["scenario"], "lost_alerts", r["lost_alerts"], None, f"dup={r['duplicate_alerts']} cases_same={r['cases_identical_to_clean_run']}")
        c = fi["config"]
        out += [f"### Fault injection ({c['patients']} patients x {c['days_each']} days; clean run {c['ref_wall_s']} s)",
                table(["Fault", "Lost alerts", "Extra alerts", "Duplicates", "Falls alerted", "Detect (s)", "Recover (s)",
                       "Cases identical to clean run", "Differ only in quality flag", "Differ otherwise", "Pass-through after kill (s)",
                       "Agent 1-2 outputs without Agent 0", "Report modes", "Wall overhead (s)"], body)]
    return "\n\n".join(out) + "\n"


def _ablation(a2) -> str:
    au = a2["within_patient_audit"]
    return (f"per-patient z-scores only: pooled ROC-AUC {au['z_only']['pooled_roc_auc']:.3f}, PR-AUC {au['z_only']['pooled_pr_auc']:.3f}; "
            f"drift score only: pooled ROC-AUC {au['drift_score_only']['pooled_roc_auc']:.3f} (chance = 0.5).")


def _drop_sentence(au) -> str:
    fz, zz = au["full"], au["z_only"]
    return (f"Dropping absolute levels moves pooled ROC {fz['pooled_roc_auc']:.3f} to {zz['pooled_roc_auc']:.3f}, while within-patient ROC "
            f"moves {fz['within_patient']['roc_auc_median_q1_q3'][0]:.3f} to {zz['within_patient']['roc_auc_median_q1_q3'][0]:.3f}: "
            "absolute levels mostly separate patients; personal-baseline z-scores carry the within-patient signal.")


def sync_results(md: str, path="reports/RESULTS.md") -> int:
    """Replace RESULTS.md lines whose generated counterpart changed (matched on the line's leading text / first table cell).
    Prose lines are left alone; every number in a replaced line comes from a metrics JSON."""
    cur = Path(path).read_text().splitlines()
    have, n = set(cur), 0
    for g in md.split("### Fault injection")[0].splitlines():
        if not g.strip() or g in have:
            continue
        key = g.split("|")[1].strip() if g.startswith("|") else g[:30]
        for i, l in enumerate(cur):
            if (l.startswith("|") and l.split("|")[1].strip() == key) or (not g.startswith("|") and l.startswith(key)):
                cur[i], n = g, n + 1
                break
    Path(path).write_text("\n".join(cur) + "\n")
    return n


def main():
    rows.clear()
    md = build()
    if "--sync" in __import__("sys").argv:
        print("RESULTS.md lines updated:", sync_results(md))
    pd.DataFrame(rows).to_csv("reports/results_table.csv", index=False)
    Path("reports/results_table.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
