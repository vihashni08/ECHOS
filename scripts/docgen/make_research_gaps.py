"""Write the 'Research gaps and what we measured' table from reports/metrics/*.json (numbers are read, never typed).
Output: docs/RESEARCH_GAPS.md, and the same table between <!-- research_gaps:start/end --> in README.md.
Usage: PYTHONPATH=. python scripts/docgen/make_research_gaps.py"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
M = ROOT / "reports" / "metrics"


def J(name):
    return json.loads((M / f"{name}.json").read_text())


def ci(x, lo_hi, d=2):
    return f"{x:.{d}f} ({lo_hi[0]:.{d}f}-{lo_hi[1]:.{d}f})"


def pct(x, d=1):
    return f"{100 * x:.{d}f}%"


def fault_summary(fi) -> str:
    c = fi["cases"]
    clean = set(c["clean"]["alerts"])
    cases = [v for k, v in c.items() if k != "clean"]
    clean_ok = sum(1 for v in cases if set(v["alerts"]) == clean and not v["outbox_duplicates"] and not v["mail_duplicates"])
    failed = {v["label"]: v["outbox_status"].get("FAILED", 0) for v in cases if v["outbox_status"].get("FAILED")}
    s = f"{clean_ok} of {len(cases)} fault cases (worker / supervisor / API kills, LLM and PubMed failures, SMTP failures) lost no alert and made no duplicate"
    if failed:
        s += "; exception: " + "; ".join(f"{k}: {n} outbox rows FAILED (undelivered, visible; measured before the 15-minute retry window and 2-hour sweeper existed)" for k, n in failed.items())
    return s


def build() -> str:
    a1, e2e, ab, fe = J("agent1"), J("e2e"), J("ablation"), J("fall_eval")
    fi, gate, dq, lat = J("fault_injection"), J("gate_eval"), J("data_quality"), J("alert_latency")
    r1, c1 = a1["results"], a1["bootstrap_95ci"]
    vit = ab["tables"]["vitals_labelled_days"]
    bud = "A's budget"
    rule, sub = vit[bud]["A fixed BP rule"], vit[bud]["B vitals model only"]
    one = vit["1.0 / pt-wk"]["B vitals model only"]
    rt = J("report_text_check")
    nr_share = dq["days_without_reading"] / dq["patient_days"]
    nl, ld = lat["no_load"], lat["load_4_busy_threads"]
    fall_ci = e2e["falls_detected_pct_ci95_wilson"]
    rows = [
        ("Raw alerts without context or explanation",
         "Every vitals and fall alert is shown with its values against the person's own usual, a plain-words 'why it fired' line, SHAP top features and persistence evidence; "
         "the daily and weekly reports cite guideline sources per finding.",
         f"Alert burden with context attached: MONITOR days {pct(e2e['status_share']['MONITOR'])}, URGENT {pct(e2e['status_share']['URGENT'])} of {e2e['patient_days']} replayed patient-days; "
         f"vitals sub-model at 1 alert/patient-week: recall {ci(one['recall'], one['recall_ci95'])} vs chance {one['chance_recall']:.2f}. "
         f"Automated text proxy (not a reader study) on {rt['n_reports']} template daily reports: median reading grade {rt['reading_grade']['median']}, median length {rt['words']['median']} words, "
         f"all checks passed in {rt['all_checks_pass']}/{rt['n_reports']} (grade <= {rt['criteria']['max_grade']:g}, <= {rt['criteria']['max_words']} words, named next steps, no banned wording).",
         "Whether the explanations are clear or useful to a reader has NOT been measured (rating kit prepared in reports/rating, not run); the text proxy checks wording rules of simple template text, not understanding, and says nothing about LLM wording. Replay, not live use."),
        ("Fixed thresholds ignore the individual",
         "Past-only personal baselines (shift(1)); XGBoost on deviation from the person's own usual, not on absolute values alone.",
         f"Vitals PR-AUC {ci(r1['xgboost']['pr_auc'] if 'xgboost' in r1 else a1['results']['at_rule_budget']['pr_auc'], c1['xgb_pr_auc'], 3)} vs fixed BP rule {ci(r1['fixed_rule']['pr_auc'], c1['rule_pr_auc'], 3)}; "
         f"at the rule's own alert count recall {ci(sub['recall'], sub['recall_ci95'])} vs {ci(rule['recall'], rule['recall_ci95'])} (out-of-fold, patient-level folds, patient bootstrap CI; the recall CIs overlap).",
         "Labels are described as clinician-verified events but we did not verify how they were produced, and 99% of BP-label days have a BP reading, so vitals results are an upper bound on generalisation; 54 patients."),
        ("Fragile pipelines and missing data",
         "Rule-based Data Quality agent (ranges, gaps, completeness; never imputes); a day with unchecked data / monitoring degraded / >7 days without vitals is DATA_GAP and never Normal; "
         "supervisor restarts dead workers; documented fallbacks.",
         f"{dq['days_without_reading']} of {dq['patient_days']} patient-days ({pct(nr_share)}) have no BP/HR reading, {dq['days_vitals_gap_over_max']} are more than 7 days after the last one (DATA_GAP); replay data-gap days {pct(e2e['data_gap_share_any_status'])}, "
         f"{e2e['data_gap_flag_vs_reconstruction_mismatches']} flag mismatches. Fault injection: {fault_summary(fi)}.",
         "3 patients x 10 days, one fault per case, fakes for LLM / SMTP / PubMed, one machine; the supervisor is a single point of failure for restarts."),
        ("Unsafe LLM text",
         "The LLM never decides emergencies and is not on the alert path; reports pass a non-LLM validator (numbers from the data, cited ids, no diagnosis / medication advice); "
         "NORMAL days use a template with no LLM; any failure falls back to the template.",
         f"Gate: {gate['reports_routed_to_llm']['avoided']} of {gate['patient_days']} days ({pct(gate['reports_routed_to_llm']['avoided_share'])}) need no LLM; triage identical with the gate on and off: "
         f"{gate['triage_identical']}; alerts missed due to gating: {gate['alerts_missed_due_to_gating']}. LLM errors and timeouts (fake): reports fell back to the template, no alert lost.",
         "Quality of real model output was NOT evaluated (only fakes in tests; the live check is recorded separately). The validator catches the rules it encodes, not every unsafe sentence."),
        ("Alert delivery and delays",
         "Fall and critical-vitals alerts are queued before any report work as a fixed template (no LLM); outbox with retries (backoff, 15-minute window, 2-hour sweeper); dedup per event.",
         f"Software latency, event to queued median {nl['event_to_queued']['median_s'] * 1000:.1f} ms, to sent (fake mailer) median {nl['event_to_sent']['median_s'] * 1000:.0f} ms (p95 {nl['event_to_sent']['p95_s'] * 1000:.0f} ms); "
         f"under 4 busy threads median {ld['event_to_sent']['median_s']:.2f} s (p95 {ld['event_to_sent']['p95_s']:.2f} s). "
         f"Injected falls raised an URGENT alert in {e2e['falls_detected_as_urgent']}/{e2e['injected_falls']} = {ci(e2e['fall_detection_rate'], fall_ci, 3)}; "
         f"false fall alerts {e2e['false_fall_alerts_per_patient_week']} per patient-week.",
         f"Replay delay (median {e2e['detection_delay_sim_hours_median']} h, max {e2e['detection_delay_sim_hours_max']} h) is a daily-batch artefact; the live path is sub-second. Real SMTP was never exercised; "
         "falls are simulated SisFall windows; 4 held-out subjects."),
    ]
    out = ["| Gap | Mechanism | Measured evidence (95% CI) | Caveat |", "|---|---|---|---|"]
    out += ["| " + " | ".join(c.replace("|", "/") for c in r) + " |" for r in rows]
    out += ["", "**Not claimed:** clinical validity (nothing here was validated against clinical outcomes or in a clinical setting); deterioration forecasting "
            "(the models detect same-day clinical-alert labels, they do not predict future events); algorithmic novelty (standard XGBoost / random forest / rules on personal baselines; "
            "the contribution is the safety-oriented system around them). Decision support, not diagnosis."]
    return "\n".join(out)


def main():
    t = build()
    (ROOT / "docs" / "RESEARCH_GAPS.md").write_text("# Research gaps and what we measured\n\nGenerated by `scripts/docgen/make_research_gaps.py` from `reports/metrics/*.json`; "
                                                    "do not edit by hand.\n\n" + t + "\n")
    p = ROOT / "README.md"
    s = p.read_text()
    blk = "<!-- research_gaps:start -->\n" + t + "\n<!-- research_gaps:end -->"
    if "<!-- research_gaps:start -->" in s:
        s = re.sub(r"<!-- research_gaps:start -->.*?<!-- research_gaps:end -->", lambda m: blk, s, flags=re.S)
        p.write_text(s)
    print(t)


if __name__ == "__main__":
    main()
