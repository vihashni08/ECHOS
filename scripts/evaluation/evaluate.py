"""Merge reports/metrics/*.json into one results table (reports/results_table.md + reports/results_table.csv).

Usage: PYTHONPATH=. python3 scripts/evaluation/evaluate.py     (re-run the agents' own evaluate() first to refresh the JSONs)
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
        out += [f"### Vitals sub-model, out-of-fold, patient-level GroupKFold. {a1['n_reading_days']} reading-days, {a1['n_patients']} patients, "
                f"prevalence {a1['prevalence']:.3f}", table(["Method", "PR-AUC (95% CI)", "Recall (95% CI)", "False alerts / patient-week", "Alerts"], body),
                "SHAP top features: " + ", ".join(f"{k} {v:.2f}" for k, v in a1["shap_global_top5"].items())]
    a3 = load("agent3")
    if a3:
        body = []
        for g, v in a3["groups"].items():
            ci = wilson(round(v["sensitivity"] * v["n_falls"]), v["n_falls"])
            body.append([g, len(v["fall_subjects"]), v["n_falls"], f(v["sensitivity"], ci), f(v["specificity_windows"], d=4), f(v["false_alarms_per_hour"], d=2)])
            add("agent3", g, "sensitivity", v["sensitivity"], ci)
            add("agent3", g, "specificity", v["specificity_windows"])
            add("agent3", g, "false_alarms_per_hour", v["false_alarms_per_hour"])
        out += [f"### Fall agent (SisFall), subject-wise out-of-fold, threshold {a3['threshold']}. Overall PR-AUC {a3['pr_auc']:.3f}, "
                f"{a3['n_subjects']} subjects",
                table(["Group", "Fall subjects", "Fall trials", "Sensitivity (Wilson 95% CI)", "Specificity (windows)", "False alarms / hour"], body),
                "Wilson CIs treat trials as independent; trials are clustered within subjects, so true uncertainty is wider (elderly = one subject)."]
    e = load("e2e")
    if e:
        body = [["Patients / patient-days / patient-weeks", f"{e['patients']} / {e['patient_days']} / {e['patient_weeks']}"],
                ["Injected falls", e["injected_falls"]],
                ["Fall detection rate (URGENT, fall reason)", f(e["fall_detection_rate"], e["falls_detected_pct_ci95_wilson"]) + f" ({e['falls_detected_as_urgent']}/{e['injected_falls']})"],
                ["Detection delay, simulated (fall -> end of day), median / max", f"{e['detection_delay_sim_hours_median']} h / {e['detection_delay_sim_hours_max']} h"],
                ["False fall alerts per patient-week", f"{e['false_fall_alerts_per_patient_week']} ({e['false_fall_alerts']} alerts)"],
                ["URGENT days from critical vitals (real TIHM readings)", e["urgent_critical_vitals_days"]],
                ["All URGENT alerts per patient-week", e["urgent_alerts_per_patient_week"]],
                ["Days Normal / Monitor / Urgent", " / ".join(f"{e['status_share'].get(k, 0):.1%}" for k in ("NORMAL", "MONITOR", "URGENT"))],
                ["Days with a data gap (any status)", f"{e['data_gap_share_any_status']:.1%}"], ["Duplicate alerts", e["duplicate_alerts"]]]
        for r_ in body:
            add("e2e", r_[0], "value", None, None, str(r_[1]))
        out += ["### End-to-end replay (systems run, full period, injected falls)", table(["Measure", "Value"], body)]
        if e.get("monitor_days_by_reason"):
            nm = sum(e["monitor_days_by_reason"].values())
            out += [f"MONITOR days by reason ({nm} days)", table(["Reason", "Days", "Share of MONITOR days"],
                    [[k, v, f"{v / nm:.1%}"] for k, v in e["monitor_days_by_reason"].items()]),
                    "DATA_GAP causes, all statuses (a day can have several)", table(["Cause", "Days"], [[k, v] for k, v in e["data_gap_days_by_cause"].items()])]
    mt = load("monitor_thresholds")
    if mt:
        o, n = mt["old"], mt["new"]
        out += ["### Vitals MONITOR threshold re-selected from out-of-fold predictions (alert budget)",
                f"Budget: vitals <= 1 alert per patient-week. Score scale: {mt.get('score_scale', 'raw')}.",
                table(["", "Previous (config)", "Current"],
                      [["vitals_prob", o["vitals_prob"], n["vitals_prob"]],
                       ["Vitals alerts / patient-week", f"{o['vitals_alerts_per_patient_week']:.2f}", f"{n['vitals_alerts_per_patient_week']:.2f}"],
                       ["Vitals recall (BP/pulse/water labels)", f"{o['vitals_recall']:.3f}", f"{n['vitals_recall']:.3f}"]])]
    return "\n\n".join(out) + "\n"


def sync_results(md: str, path="reports/RESULTS.md") -> int:
    """Replace RESULTS.md lines whose generated counterpart changed (matched on the line's leading text / first table cell).
    Prose lines are left alone; every number in a replaced line comes from a metrics JSON."""
    txt = Path(path).read_text()
    nxt = lambda s, i: min([j for j in (s.find("\n### ", i + 1), s.find("\n## ", i + 1), s.find("\n<!--", i + 1)) if j > 0] or [len(s)])   # noqa: E731  next heading or block marker
    for head in ("### End-to-end replay", "### Vitals MONITOR threshold re-selected"):      # whole generated sections: replaced, not patched line by line
        if head in md and head in txt:
            g0, c0 = md.index(head), txt.index(head)
            txt = txt[:c0] + md[g0:nxt(md, g0)].rstrip("\n") + "\n" + txt[nxt(txt, c0):]
    Path(path).write_text(txt)
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


def sync_readme(path="README.md") -> int:
    """README headline bullet for the fall agent, from agent3.json (never hand-typed)."""
    import re
    g = load("agent3")["groups"]
    new = f"sensitivity {g['adult']['sensitivity']:.3f} adults / {g['elderly']['sensitivity']:.3f} elderly"
    t = Path(path).read_text()
    t2 = re.sub(r"sensitivity [\d.]+ adults / [\d.]+ elderly", new, t)
    e = load("e2e")
    sh = e["status_share"]
    t2 = re.sub(r"- End-to-end \([^)]*\): \d+ detected \(\d+%\); days are \d+% Normal / \d+% Monitor / \d+% Urgent",
                f"- End-to-end ({e['patients']} patients, {e['injected_falls']} injected falls): {e['falls_detected_as_urgent']} detected ({e['fall_detection_rate']:.0%}); "
                f"days are {sh['NORMAL']:.0%} Normal / {sh['MONITOR']:.0%} Monitor / {sh['URGENT']:.0%} Urgent", t2)
    fi = load("fault_injection")
    if fi and "cases" in fi:
        cs, ref = fi["cases"], fi["cases"]["clean"]
        n = len(cs) - 1
        ok = sum(1 for k, r in cs.items() if k != "clean" and not (set(ref["alerts"]) - set(r["alerts"])) and r["outbox_duplicates"] == 0 and r["mail_duplicates"] == 0)
        t2 = re.sub(r"- Fault injection: .*", f"- Fault injection (fakes, {fi['config']['patients']} patients x {fi['config']['days']} days): no alert lost and no duplicate in {ok} of {n} fault cases "
                    "(the SMTP outage case, measured before the retry window was added, leaves undelivered rows FAILED instead; see RESULTS.md).", t2)
    Path(path).write_text(t2)
    return int(t2 != t)


def main():
    rows.clear()
    md = build()
    if "--sync" in __import__("sys").argv:
        print("RESULTS.md lines updated:", sync_results(md), "README updated:", sync_readme())
    pd.DataFrame(rows).to_csv("reports/results_table.csv", index=False)
    Path("reports/results_table.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
