"""Fall window audit: peak-position bias check, old (v1) vs unbiased (v2) windowing on identical subject folds,
threshold curve with / without post-fall inactivity confirmation, operating-point choice.
Usage: PYTHONPATH=. python scripts/evaluation/fall_audit.py [--write-config]  (writes reports/metrics/fall_audit.json; no model is saved here)"""
import json
import sys
import warnings

import numpy as np
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
from echos.agents import fall as F  # noqa: E402
from echos.config import ROOT, load_config  # noqa: E402

OUT = ROOT / "reports/metrics/fall_audit.json"
THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9]
FA_MAX, SENS_MIN = 0.5, 0.90


def audit_text():
    cfg = load_config()["agent3"]
    n = int(cfg["window_s"] * cfg["fs"])
    print(f"v1 windows: fall = ONE window per fall trial centred EXACTLY on the max-SVM sample (peak at index {n // 2} of {n}); "
          f"ADL = sliding windows, stride {cfg['stride_s']} s, peak anywhere.")
    print(f"v2 windows: fall and ADL peak windows both centred on own max sample +/- {cfg['jitter_s']} s jitter (same code path); "
          "second ADL set = the sliding windows.")
    print("features:", ", ".join(F.FEATURES))
    print("peak-position dependence: peak_svm/mean_svm/std_*/mean_*: none. jerk_max: indirect. post_impact_std and post_impact_mean_dev are "
          "computed on s[pk+0.25 s:], so their length (and the fallback when pk is late) depends on the peak position;\n"
          "post_inactive flag is measured from the peak.")


def peak_bias(v1, v2):
    """How well does 'where is the peak in the window' alone separate the classes? (AUC 0.5 = not at all)"""
    n2 = int(load_config()["agent3"]["window_s"] * load_config()["agent3"]["fs"]) // 2
    slide = v2[v2.kind == "adl_slide"]
    # v1: falls always at n/2 (distance 0); ADL = sliding windows
    d1 = np.r_[np.zeros(int(v1.y.sum())), (slide.peak_pos - n2).abs().values]
    y1 = np.r_[np.ones(int(v1.y.sum())), np.zeros(len(slide))]
    m = v2.kind.isin(["fall", "adl_peak"])
    d2 = (v2[m].peak_pos - n2).abs().values
    out = {"v1_auc_of_peak_distance_from_centre": float(roc_auc_score(y1, -d1)),
           "v2_auc_of_peak_distance_from_centre": float(roc_auc_score(v2[m].y, -d2)),
           "v1_fall_peak_offset_samples": "always 0",
           "v2_fall_peak_offset_abs_median": float(np.median((v2[v2.kind == 'fall'].peak_pos - n2).abs())),
           "v2_adl_peak_offset_abs_median": float(np.median((v2[v2.kind == 'adl_peak'].peak_pos - n2).abs())),
           "sliding_adl_peak_offset_abs_median": float(np.median((slide.peak_pos - n2).abs()))}
    per = {}
    for f in F.FEATURES:
        a1 = roc_auc_score(np.r_[np.ones(int(v1.y.sum())), np.zeros(len(slide))], np.r_[v1[v1.y == 1][f].values, slide[f].values])
        a2 = roc_auc_score(v2[m].y, v2[m][f])
        per[f] = [float(max(a1, 1 - a1)), float(max(a2, 1 - a2))]
    out["single_feature_auc_v1_v2"] = per
    return out


def main(write_config=False):
    cfg = load_config()
    seed = cfg["seed"]
    audit_text()
    v1 = F.with_kind(F.build_windows().reset_index(drop=True))
    v2 = F.build_windows_v2().reset_index(drop=True)
    print(f"v1: {len(v1)} windows ({int(v1.y.sum())} falls); v2: {len(v2)} ({v2.kind.value_counts().to_dict()}), subjects {v1.subject.nunique()}/{v2.subject.nunique()}")
    bias = peak_bias(v1, v2)
    print("peak-position AUC (0.5 = no leak): v1 %.3f -> v2 %.3f" % (bias["v1_auc_of_peak_distance_from_centre"], bias["v2_auc_of_peak_distance_from_centre"]))
    top = sorted(bias["single_feature_auc_v1_v2"].items(), key=lambda kv: -kv[1][0])[:4]
    print("top single-feature AUC v1->v2:", "; ".join(f"{k} {a:.3f}->{b:.3f}" for k, (a, b) in top))

    o1, o2 = F.score_oof(v1, seed), F.score_oof(v2, seed)
    old, new = F.metrics_at(v1, o1, 0.5), F.metrics_at(v2, o2, 0.5)
    rows = [("PR-AUC (falls vs ADL windows as built)", old["pr_auc"], new["pr_auc"]),
            ("PR-AUC (falls vs sliding ADL)", old["pr_auc_vs_sliding"], new["pr_auc_vs_sliding"]),
            ("adult sensitivity", old["groups"]["adult"]["sensitivity"], new["groups"]["adult"]["sensitivity"]),
            ("adult specificity (as-built ADL windows)", old["groups"]["adult"]["specificity_windows"], new["groups"]["adult"]["specificity_windows"]),
            ("adult specificity (sliding windows)", old["groups"]["adult"]["specificity_sliding_windows"], new["groups"]["adult"]["specificity_sliding_windows"]),
            ("elderly sensitivity (1 subject, SE06)", old["groups"]["elderly"]["sensitivity"], new["groups"]["elderly"]["sensitivity"]),
            ("adult false alarms / hour", old["groups"]["adult"]["false_alarms_per_hour"], new["groups"]["adult"]["false_alarms_per_hour"])]
    print(f"\n{'RF @0.5, subject folds':<44}{'old v1':>9}{'new v2':>9}")
    for n, a, b in rows:
        print(f"{n:<44}{a:>9.3f}{b:>9.3f}")

    curve = []
    for confirm in (False, True):
        for t in THRESHOLDS:
            m = F.metrics_at(v2, o2, t, confirm)["groups"]["adult"]
            curve.append({"threshold": t, "confirm_inactivity": confirm, "adult_sensitivity": m["sensitivity"],
                          "adult_false_alarms_per_hour": m["false_alarms_per_hour"]})
    print(f"\n{'thr':>5} | {'sens':>6} {'FA/h':>6} (no confirmation) | {'sens':>6} {'FA/h':>6} (inactivity-confirmed)")
    for i, t in enumerate(THRESHOLDS):
        a, b = curve[i], curve[i + len(THRESHOLDS)]
        print(f"{t:>5} | {a['adult_sensitivity']:>6.3f} {a['adult_false_alarms_per_hour']:>6.2f} {'':<20}| {b['adult_sensitivity']:>6.3f} {b['adult_false_alarms_per_hour']:>6.2f}")

    ok = [c for c in curve if not c["confirm_inactivity"] and c["adult_false_alarms_per_hour"] <= FA_MAX]
    best = max(ok, key=lambda c: c["adult_sensitivity"]) if ok else None
    if best and best["adult_sensitivity"] >= SENS_MIN:
        chosen, why = best, f"unconfirmed: highest sensitivity with adult FA/h <= {FA_MAX} (sens {best['adult_sensitivity']:.3f} >= {SENS_MIN})"
    else:
        conf = [c for c in curve if c["confirm_inactivity"]]
        okc = [c for c in conf if c["adult_false_alarms_per_hour"] <= FA_MAX]
        chosen = max(okc, key=lambda c: c["adult_sensitivity"]) if okc else min(conf, key=lambda c: c["adult_false_alarms_per_hour"])
        why = (f"tradeoff: no unconfirmed threshold has FA/h <= {FA_MAX} with sensitivity >= {SENS_MIN} "
               f"(best unconfirmed: {'none under the FA limit' if not best else 'thr %.1f sens %.3f FA/h %.2f' % (best['threshold'], best['adult_sensitivity'], best['adult_false_alarms_per_hour'])}); "
               "inactivity-confirmed setting chosen")
    print(f"\nCHOSEN: threshold {chosen['threshold']} require_inactivity={chosen['confirm_inactivity']} -> sens {chosen['adult_sensitivity']:.3f}, "
          f"FA/h {chosen['adult_false_alarms_per_hour']:.2f}. {why}")

    d_pr, d_sens, d_fa = abs(new["pr_auc"] - old["pr_auc"]), abs(new["groups"]["adult"]["sensitivity"] - old["groups"]["adult"]["sensitivity"]), \
        abs(new["groups"]["adult"]["false_alarms_per_hour"] - old["groups"]["adult"]["false_alarms_per_hour"])
    material = bool(d_pr > 0.01 or d_sens > 0.02 or d_fa > 0.25)
    print(f"material change (|dPR-AUC|>0.01 or |dSens|>0.02 or |dFA/h|>0.25): {material} (d={d_pr:.3f}, {d_sens:.3f}, {d_fa:.2f})")

    res = {"peak_bias": bias, "old_v1": old, "new_v2": new, "curve": curve, "chosen": {**chosen, "reason": why}, "material_change": material,
           "thresholds": THRESHOLDS, "rule": {"fa_max": FA_MAX, "sens_min": SENS_MIN},
           "note": "SisFall ADL trials are short vigorous trials; false alarms per hour overstates daily life."}
    OUT.write_text(json.dumps(res, indent=2, default=float))
    if write_config:
        patch_config(chosen)
        write_docs(res)


def write_docs(res: dict):
    """Script-generated RESULTS.md section (every number comes from fall_audit.json)."""
    o, n, b = res["old_v1"], res["new_v2"], res["peak_bias"]
    ag, bg = o["groups"], n["groups"]
    rows = [("PR-AUC (falls vs ADL windows as built)", o["pr_auc"], n["pr_auc"]), ("PR-AUC (falls vs sliding ADL)", o["pr_auc_vs_sliding"], n["pr_auc_vs_sliding"]),
            ("Adult sensitivity", ag["adult"]["sensitivity"], bg["adult"]["sensitivity"]),
            ("Adult specificity (ADL windows as built)", ag["adult"]["specificity_windows"], bg["adult"]["specificity_windows"]),
            ("Adult specificity (sliding windows)", ag["adult"]["specificity_sliding_windows"], bg["adult"]["specificity_sliding_windows"]),
            ("Elderly sensitivity (one subject, SE06)", ag["elderly"]["sensitivity"], bg["elderly"]["sensitivity"]),
            ("Adult false alarms / hour", ag["adult"]["false_alarms_per_hour"], bg["adult"]["false_alarms_per_hour"])]
    md = ["<!-- fall_audit:start -->", "## Fall window audit (written by scripts/evaluation/fall_audit.py)", "",
          f"v1 fall windows were centred exactly on the max-magnitude sample while ADL windows were sliding windows, so the peak's position alone separated the classes "
          f"(AUC of |peak offset from window centre|: v1 {b['v1_auc_of_peak_distance_from_centre']:.3f}, v2 {b['v2_auc_of_peak_distance_from_centre']:.3f}). "
          f"v2 builds falls and ADL peak windows identically (own max sample, same length, +/- 0.5 s jitter) and keeps sliding ADL windows for false-alarm exposure. "
          "Random forest, threshold 0.5, identical subject-level folds:", "",
          "| Measure | v1 (old) | v2 (new) |", "|---|---|---|"] + [f"| {a} | {x:.3f} | {y:.3f} |" for a, x, y in rows] + ["",
          "Adult threshold curve, v2 windows (sensitivity / false alarms per hour):", "",
          "| Threshold | No confirmation | With post-fall inactivity |", "|---|---|---|"]
    T = res["thresholds"]
    for i, t in enumerate(T):
        a, c = res["curve"][i], res["curve"][i + len(T)]
        md.append(f"| {t} | {a['adult_sensitivity']:.3f} / {a['adult_false_alarms_per_hour']:.2f} | {c['adult_sensitivity']:.3f} / {c['adult_false_alarms_per_hour']:.2f} |")
    ch = res["chosen"]
    md += ["", f"Chosen operating point: threshold {ch['threshold']}, inactivity confirmation {'required' if ch['confirm_inactivity'] else 'not required'} "
           f"(adult sensitivity {ch['adult_sensitivity']:.3f}, {ch['adult_false_alarms_per_hour']:.2f} false alarms/h). Rule: {res['rule']['fa_max']} false alarms/h or fewer with "
           f"sensitivity >= {res['rule']['sens_min']} if possible, otherwise the inactivity-confirmed setting. {ch['reason']}.", "",
           "SisFall ADL trials are short, vigorous trials (running, jumping, stairs, sit-downs), so alarms per hour overstate daily life; "
           "SisFall falls are scripted on mats by young volunteers, and only one elderly subject performed falls.", "<!-- fall_audit:end -->"]
    p = ROOT / "reports/RESULTS.md"
    t = p.read_text()
    block = "\n".join(md)
    if "<!-- fall_audit:start -->" in t:
        a_, b_ = t.index("<!-- fall_audit:start -->"), t.index("<!-- fall_audit:end -->") + len("<!-- fall_audit:end -->")
        t = t[:a_] + block + t[b_:]
    else:
        t = t.rstrip("\n") + "\n\n" + block + "\n"
    p.write_text(t)


def patch_config(chosen: dict):
    """agent3.operating + the triage fall thresholds follow the chosen operating point (no number is typed by hand).
    require_inactivity -> an unconfirmed fall is never URGENT on its own (triage.fall_prob_high: null)."""
    import re
    p = ROOT / "config.yaml"
    s = p.read_text()
    thr, conf = chosen["threshold"], bool(chosen["confirm_inactivity"])
    s = re.sub(r"  operating: \{[^}]*\}[^\n]*", f"  operating: {{threshold: {thr}, require_inactivity: {str(conf).lower()}}}"
               "   # chosen by scripts/evaluation/fall_audit.py (reports/metrics/fall_audit.json)", s)
    s = re.sub(r"  fall_prob_high: [^\n#]*#[^\n]*", "  fall_prob_high: " + ("null" if conf else str(thr)) +
               "   # URGENT on probability alone; null = inactivity confirmation required (scripts/evaluation/fall_audit.py)", s)
    s = re.sub(r"  fall_prob: [^\n#]*#[^\n]*", f"  fall_prob: {thr}" + "   # URGENT with post-fall inactivity (MONITOR without, if fall_prob_high is null); scripts/evaluation/fall_audit.py", s)
    s = re.sub(r"  model_file: [^\n]*\n", "", s)
    s = s.replace("  jitter_s:", f"  model_file: fall_v2.joblib   # v2 (unbiased windowing) model; fall.joblib = v1, kept\n  jitter_s:", 1)
    p.write_text(s)
    print("config.yaml updated")


if __name__ == "__main__":
    if "--config-only" in sys.argv:
        _r = json.loads(OUT.read_text())
        patch_config(_r["chosen"])
        write_docs(_r)
    else:
        main("--write-config" in sys.argv)
