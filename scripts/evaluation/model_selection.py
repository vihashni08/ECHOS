"""Model selection tables on IDENTICAL folds (patient-level GroupKFold for TIHM sub-models, subject-level for SisFall).
Fixed small grids chosen by inner CV on the training part of each outer fold; bootstrap 95% CIs resample patients/subjects;
paired bootstrap of the difference vs the CURRENT model decides whether to switch (CI of the difference must be above zero).
Does not save or overwrite any model. Writes reports/metrics/model_selection.json and a section of reports/RESULTS.md.
Usage: PYTHONPATH=. python scripts/evaluation/model_selection.py [vitals|fall ...]"""
import json
import sys
import warnings

import numpy as np
import xgboost as xgb
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from echos.agents import fall as F
from echos.agents.health import vitals as V
from echos.config import ROOT, load_config
from echos.data.guard import assert_real_source
from echos.data.patient_day import load_patient_day
from echos.data.splits import patient_folds, subject_folds

warnings.filterwarnings("ignore")
cfg = load_config()
SEED, NF, NB = cfg["seed"], cfg["splits"]["n_folds"], 200
OUT = ROOT / "reports/metrics/model_selection.json"

try:
    import lightgbm as lgb
    LGB_NOTE = None
except Exception as ex:  # noqa: BLE001
    lgb, LGB_NOTE = None, f"lightgbm not used: import failed ({type(ex).__name__}: {ex})"


def make(name, p, pos_w, seed=SEED):
    if name == "logreg":
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=p["C"], class_weight="balanced", max_iter=2000))
    cls = {"random_forest": RandomForestClassifier, "extra_trees": ExtraTreesClassifier}.get(name)
    if cls:
        return make_pipeline(SimpleImputer(strategy="median"), cls(n_estimators=200, max_depth=p["max_depth"], min_samples_leaf=p["leaf"],
                                                                  class_weight="balanced_subsample", random_state=seed, n_jobs=2))
    if name == "xgboost":     # the CURRENT configuration (single grid point)
        return V._model(seed, pos_w)
    if name == "lightgbm":
        return lgb.LGBMClassifier(n_estimators=200, max_depth=p["max_depth"], learning_rate=0.05, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                                  min_child_samples=5, scale_pos_weight=pos_w, random_state=seed, n_jobs=2, verbose=-1)


TREE = [dict(max_depth=4, leaf=3), dict(max_depth=8, leaf=3)]
GRIDS = {"logreg": [dict(C=0.1), dict(C=1.0)], "random_forest": TREE, "extra_trees": TREE, "lightgbm": [dict(max_depth=2), dict(max_depth=3)]}


def fit_predict(name, grid, which, dtr, ytr, dte, feats, group_col="patient_id"):
    pw = lambda y: (1 - y.mean()) / max(y.mean(), 1e-6)
    if name == "xgboost":
        return make(name, {"which": which}, pw(ytr)).fit(dtr[feats], ytr).predict_proba(dte[feats])[:, 1]
    best, best_s = grid[0], -1
    if len(grid) > 1:
        for p in grid:
            sc = []
            for a, b in patient_folds(dtr.reset_index(drop=True), 3, SEED):
                if ytr[a].sum() == 0 or ytr[b].sum() == 0:
                    continue
                m = make(name, p, pw(ytr[a])).fit(dtr.iloc[a][feats], ytr[a])
                sc.append(average_precision_score(ytr[b], m.predict_proba(dtr.iloc[b][feats])[:, 1]))
            if sc and np.mean(sc) > best_s:
                best, best_s = p, float(np.mean(sc))
    return make(name, best, pw(ytr)).fit(dtr[feats], ytr).predict_proba(dte[feats])[:, 1]


def topk(y, s, k):
    k = int(min(max(k, 1), len(y)))
    return int(y[np.argsort(-s, kind="stable")[:k]].sum()), k


def ci(a):
    a = np.asarray(a, float)
    return [float(np.nanpercentile(a, 2.5)), float(np.nanpercentile(a, 97.5))]


def tihm_table(which):
    pdy = load_patient_day()
    df, feats = V.build_features(pdy), V.FEATURES
    assert_real_source(df, "tihm")
    y, pids = df.y.values, df.patient_id.values
    # monitored patient-weeks per patient: vitals = all patient-days of the patient / 7 (as evaluate())
    pwk = pdy.groupby("patient_id").size() / 7.0
    names = ["logreg", "random_forest", "extra_trees", "xgboost"] + (["lightgbm"] if lgb else [])
    scores = {}
    scores["fixed_rule"] = V.fixed_rule(df).astype(float)
    folds = list(patient_folds(df, NF, SEED))
    for n in names:
        o = np.zeros(len(df))
        for tr, te in folds:
            o[te] = fit_predict(n, GRIDS.get(n, [{}]), which, df.iloc[tr], y[tr], df.iloc[te], feats)
        scores[n] = o
    up = np.unique(pids)
    idx = {p: np.where(pids == p)[0] for p in up}
    rng = np.random.RandomState(SEED)
    boots = [rng.choice(up, len(up)) for _ in range(NB)]

    def met(rows, pw, name):
        yy, ss = y[rows], scores[name][rows]
        if yy.sum() == 0:
            return None
        if name == "fixed_rule":
            k = int(ss.sum())
            tp = int(yy[ss > 0].sum())
        else:
            tp, k = topk(yy, ss, pw)       # alert budget: 1 alert / patient-week
        return {"pr_auc": average_precision_score(yy, ss), "recall": tp / yy.sum(), "fa_pw": (k - tp) / pw}

    full = np.arange(len(df))
    res, bs = {}, {n: [] for n in scores}
    for sample in boots:
        rows = np.concatenate([idx[p] for p in sample])
        pw = float(pwk.loc[sample].sum())
        for n in scores:
            bs[n].append(met(rows, pw, n))
    pw_all = float(pwk.loc[up].sum())
    for n in scores:
        pt = met(full, pw_all, n)
        b = [m for m in bs[n] if m]
        res[n] = {k: {"value": float(pt[k]), "ci95": ci([m[k] for m in b])} for k in ("pr_auc", "recall", "fa_pw")}
    cur = "xgboost"
    for n in scores:
        if n == cur:
            continue
        d = {}
        for k in ("pr_auc", "recall"):
            diff = [bs[n][i][k] - bs[cur][i][k] for i in range(NB) if bs[n][i] and bs[cur][i]]
            c = ci(diff)
            d[k] = {"diff_vs_current": float(res[n][k]["value"] - res[cur][k]["value"]), "ci95": c, "better": bool(c[0] > 0)}
        res[n]["paired_vs_current_xgboost"] = d
    better = [n for n in scores if n != cur and res[n]["paired_vs_current_xgboost"]["pr_auc"]["better"]]
    return {"n_rows": int(len(df)), "n_patients": int(len(up)), "prevalence": float(y.mean()), "patient_weeks": pw_all,
            "alert_budget": "1 alert per patient-week (fixed rule: its own alert count)", "models": res,
            "decision": {"rule": "switch only if paired-bootstrap CI of PR-AUC difference vs current XGBoost is above zero",
                         "better_than_current": better, "keep_current": not better}}


def fall_table():
    df = F.build_windows_v2().reset_index(drop=True)      # unbiased windowing: falls and ADL peak windows built identically + sliding ADL windows
    assert_real_source(df, "sisfall")
    op = cfg["agent3"]["operating"]
    THR, CONF = op["threshold"], bool(op["require_inactivity"])
    y, subj = df.y.values, df.subject.values
    kind = df.kind.values
    matched = (kind == "fall") | (kind == "adl_peak")
    win_s = cfg["agent3"]["window_s"]
    folds = list(subject_folds(df, cfg["agent3"]["n_folds"], SEED))
    scores = {}
    scores["random_forest"] = np.zeros(len(df))                       # CURRENT model, fixed
    scores["xgboost"] = np.zeros(len(df))
    grid = [dict(max_depth=3), dict(max_depth=5)]
    for tr, te in folds:
        scores["random_forest"][te] = F._rf(SEED).fit(df.iloc[tr][F.FEATURES], y[tr]).predict_proba(df.iloc[te][F.FEATURES])[:, 1]
        best, bs_ = grid[0], -1
        for p in grid:                                                # inner subject-level 3-fold selection
            sc = []
            for a, b in subject_folds(df.iloc[tr].reset_index(drop=True), 3, SEED):
                m = xgb.XGBClassifier(n_estimators=200, learning_rate=0.1, subsample=0.8, colsample_bytree=0.8, random_state=SEED, n_jobs=2,
                                      eval_metric="aucpr", scale_pos_weight=(1 - y[tr][a].mean()) / y[tr][a].mean(), **p)
                m.fit(df.iloc[tr].iloc[a][F.FEATURES], y[tr][a])
                sc.append(average_precision_score(y[tr][b], m.predict_proba(df.iloc[tr].iloc[b][F.FEATURES])[:, 1]))
            if np.mean(sc) > bs_:
                best, bs_ = p, np.mean(sc)
        m = xgb.XGBClassifier(n_estimators=200, learning_rate=0.1, subsample=0.8, colsample_bytree=0.8, random_state=SEED, n_jobs=2,
                              eval_metric="aucpr", scale_pos_weight=(1 - y[tr].mean()) / y[tr].mean(), **best)
        scores["xgboost"][te] = m.fit(df.iloc[tr][F.FEATURES], y[tr]).predict_proba(df.iloc[te][F.FEATURES])[:, 1]
    subs = np.array(sorted(set(subj)))
    grp = df.groupby("subject").group.first()
    slide_df = df[kind == "adl_slide"]
    hours = slide_df.groupby(["subject", "file"]).file_seconds.max().groupby("subject").sum() / 3600
    agg = {}
    for n, s in scores.items():
        al = (s >= THR) & (df.post_inactive.values if CONF else True)
        per = {}
        for sub in subs:
            m = subj == sub
            d_, a_, k_ = df[m], al[m], kind[m]
            sl, pk = k_ == "adl_slide", k_ == "adl_peak"
            per[sub] = {"falls": int((k_ == "fall").sum()), "fall_hit": int(a_[k_ == "fall"].sum()), "adl_w": int(pk.sum()), "adl_alert_w": int(a_[pk].sum()),
                        "hours": float(hours.get(sub, 0.0)), "events": F._alert_events(d_[sl], a_[sl]) if sl.any() else 0}
        agg[n] = per
    rng = np.random.RandomState(SEED)
    rows_by_sub = {s_: np.where(subj == s_)[0] for s_ in subs}
    boots = [rng.choice(subs, len(subs)) for _ in range(NB)]

    def gmet(per, sample, g):
        sel = [s_ for s_ in sample if grp[s_] == g]
        f = sum(per[s_]["falls"] for s_ in sel)
        w = sum(per[s_]["adl_w"] for s_ in sel)
        h = sum(per[s_]["hours"] for s_ in sel)
        return {"sensitivity": sum(per[s_]["fall_hit"] for s_ in sel) / f if f else np.nan,
                "specificity": 1 - sum(per[s_]["adl_alert_w"] for s_ in sel) / w if w else np.nan,
                "false_alarms_per_hour": sum(per[s_]["events"] for s_ in sel) / h if h else np.nan}

    res = {}
    allsubs = list(subs)
    bs = {n: [] for n in scores}
    for sample in boots:
        rows = np.concatenate([rows_by_sub[s_] for s_ in sample])
        for n in scores:
            rm = rows[matched[rows]]                                 # PR-AUC on the matched set (falls vs ADL peak windows)
            r = {"pr_auc": average_precision_score(y[rm], scores[n][rm])}
            for g in ("adult", "elderly"):
                r[g] = gmet(agg[n], sample, g)
            bs[n].append(r)
    for n in scores:
        r = {"pr_auc": {"value": float(average_precision_score(y[matched], scores[n][matched])), "ci95": ci([b["pr_auc"] for b in bs[n]])},
             "detection_delay_s": {"window_decision": win_s / 2, "with_post_fall_inactivity_confirmation": 1.0 + cfg["agent3"]["inactivity"]["seconds"],
                                   "note": "structural: each fall is one window centred on the impact, decided at window end; same for every model"}}
        for g in ("adult", "elderly"):
            pt = gmet(agg[n], allsubs, g)
            r[g] = {k: {"value": float(v), "ci95": ci([b[g][k] for b in bs[n]])} for k, v in pt.items()}
        res[n] = r
    d = {}
    for k, getter in (("pr_auc", lambda b: b["pr_auc"]), ("adult_sensitivity", lambda b: b["adult"]["sensitivity"])):
        diff = [getter(bs["xgboost"][i]) - getter(bs["random_forest"][i]) for i in range(NB)]
        c = ci(diff)
        d[k] = {"diff_xgboost_minus_rf": float(np.nanmean(diff)), "ci95": c, "better": bool(c[0] > 0)}
    res["xgboost"]["paired_vs_current_rf"] = d
    n_el = int(grp[grp == "elderly"].shape[0])
    el_fall = sorted(df[(df.group == "elderly") & (df.y == 1)].subject.unique().tolist())
    return {"n_windows": int(len(df)), "n_subjects": int(len(subs)), "adult_subjects": int((grp == "adult").sum()), "elderly_subjects": n_el,
            "elderly_fall_subjects": el_fall, "models": res,
            "threshold": THR, "require_inactivity": CONF, "windowing": "v2",
            "decision": {"rule": "switch only if paired-bootstrap CI of PR-AUC difference vs current RF is above zero",
                         "keep_current": not d["pr_auc"]["better"]}}


def fmt(v, d=3):
    return f"{v['value']:.{d}f} ({v['ci95'][0]:.{d}f}-{v['ci95'][1]:.{d}f})"


def markdown(r):
    out = ["<!-- model_selection:start -->", "## Model selection (identical folds; written by scripts/evaluation/model_selection.py)", "",
           "CIs: patient-level (TIHM) or subject-level (SisFall) bootstrap, 200 resamples. Grids: logistic C in {0.1, 1}; RF / ExtraTrees "
           "max_depth in {4, 8}; XGBoost = current configuration; fall XGBoost max_depth in {3, 5}; grids chosen by inner CV on the training part only. "
           "Calibration is monotone and does not change these rank-based numbers much; models are compared on raw scores.", ""]
    if LGB_NOTE:
        out += [f"Note: {LGB_NOTE}.", ""]
    for w, title in (("vitals", "Vitals"),):
        if w not in r:
            continue
        t = r[w]
        out += [f"### {title}: {t['n_rows']} rows, {t['n_patients']} patients, prevalence {t['prevalence']:.3f}; alert budget: {t['alert_budget']}", ""]
        out += ["| Model | PR-AUC | Recall @ budget | False alerts / patient-week |", "|---|---|---|---|"]
        for n, m in t["models"].items():
            out.append(f"| {n} | {fmt(m['pr_auc'])} | {fmt(m['recall'])} | {fmt(m['fa_pw'], 2)} |")
        dec = t["decision"]
        out += ["", "Paired difference vs current XGBoost (PR-AUC, 95% CI): " + "; ".join(
            f"{n} {m['paired_vs_current_xgboost']['pr_auc']['diff_vs_current']:+.3f} ({m['paired_vs_current_xgboost']['pr_auc']['ci95'][0]:+.3f} to {m['paired_vs_current_xgboost']['pr_auc']['ci95'][1]:+.3f})"
            for n, m in t["models"].items() if "paired_vs_current_xgboost" in m),
            f"Decision: {'keep current XGBoost' if dec['keep_current'] else 'switch candidates: ' + ', '.join(dec['better_than_current'])}.", ""]
    if "fall" in r:
        t = r["fall"]
        out += [f"### Fall (SisFall, subject-level folds, unbiased v2 windows, threshold {t['threshold']}{' + post-fall inactivity' if t['require_inactivity'] else ''}): {t['n_subjects']} subjects ({t['adult_subjects']} adult, {t['elderly_subjects']} elderly; "
                f"only {len(t['elderly_fall_subjects'])} elderly subject performed falls: {', '.join(t['elderly_fall_subjects'])})", "",
                "PR-AUC and specificity: falls vs ADL windows built identically (peak-centred); false alarms / hour: sliding ADL windows (SisFall ADL trials are short vigorous trials, so this overstates daily life).", "",
                "| Model | PR-AUC | Group | Sensitivity | Specificity (windows) | False alarms / hour | Detection delay (s) |", "|---|---|---|---|---|---|---|"]
        for n, m in t["models"].items():
            for g in ("adult", "elderly"):
                x = m[g]
                out.append(f"| {n} | {fmt(m['pr_auc'])} | {g} | {fmt(x['sensitivity'])} | {fmt(x['specificity'])} | {fmt(x['false_alarms_per_hour'], 2)} | "
                           f"{m['detection_delay_s']['window_decision']} (+{m['detection_delay_s']['with_post_fall_inactivity_confirmation']} s inactivity confirmation) |")
        p = t["models"]["xgboost"]["paired_vs_current_rf"]
        out += ["", f"Paired XGBoost minus current RF: PR-AUC {p['pr_auc']['diff_xgboost_minus_rf']:+.4f} ({p['pr_auc']['ci95'][0]:+.4f} to {p['pr_auc']['ci95'][1]:+.4f}); "
                f"adult sensitivity {p['adult_sensitivity']['diff_xgboost_minus_rf']:+.4f} ({p['adult_sensitivity']['ci95'][0]:+.4f} to {p['adult_sensitivity']['ci95'][1]:+.4f}). "
                f"Decision: {'keep current random forest' if t['decision']['keep_current'] else 'XGBoost better'}.",
                "Detection delay is structural (one window per fall, decided at window end), not measured per model.",
                "Elderly sensitivity rests on one subject's falls: its bootstrap CI is degenerate (resamples without that subject are dropped) and says nothing about elderly people in general.", ""]
    out.append("<!-- model_selection:end -->")
    return "\n".join(out)


def main(which):
    prev = json.loads(OUT.read_text()) if OUT.exists() else {}
    res = {**prev, "lightgbm": LGB_NOTE or "used"}
    for w in which:
        res[w] = fall_table() if w == "fall" else tihm_table(w)
        print(w, "done", flush=True)
    OUT.write_text(json.dumps(res, indent=2, default=float))
    md = markdown(res)
    p = ROOT / "reports/RESULTS.md"
    s = p.read_text()
    if "<!-- model_selection:start -->" in s:
        a, b = s.index("<!-- model_selection:start -->"), s.index("<!-- model_selection:end -->") + len("<!-- model_selection:end -->")
        s = s[:a] + md + s[b:]
    else:
        s = s.rstrip("\n") + "\n\n" + md + "\n"
    p.write_text(s)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:] or ["vitals", "fall"])
