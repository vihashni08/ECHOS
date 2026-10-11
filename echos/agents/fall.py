"""Agent 3 - Fall. Window classifier trained on SisFall only (subject-level splits)."""
from __future__ import annotations

import json
from datetime import date, datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from echos.data.guard import assert_real_source
from echos.config import ROOT, load_config
from echos.data import sisfall as sf
from echos.data.splits import subject_folds
from echos.schemas import AgentResult

AGENT = "fall"
MODEL_PATH_V1 = ROOT / "models" / "fall.joblib"   # v1 (biased windowing) model, kept for the record
MODEL_PATH = ROOT / "models" / load_config()["agent3"].get("model_file", "fall.joblib")
METRICS_PATH = ROOT / "reports" / "metrics" / "agent3.json"
CACHE = ROOT / "data" / "processed" / "sisfall_windows.parquet"
FEATURES = (["peak_svm", "mean_svm", "jerk_max", "post_impact_std", "post_impact_mean_dev"]
            + [f"{s}_{a}" for a in "xyz" for s in ("mean", "std")])


def window_features(w: np.ndarray, fs: int = 200) -> dict:
    """w: (n, 3) accelerometer in g."""
    s = sf.svm(w)
    pk = int(np.argmax(s))
    post = s[pk + int(0.25 * fs):]  # after the impact transient settles
    if len(post) < 10:
        post = s[pk:] if len(s[pk:]) >= 10 else s
    f = {"peak_svm": float(s[pk]), "mean_svm": float(s.mean()),
         "jerk_max": float(np.abs(np.diff(s)).max() * fs),
         "post_impact_std": float(post.std()), "post_impact_mean_dev": float(abs(post.mean() - 1.0))}
    for i, a in enumerate("xyz"):
        f[f"mean_{a}"], f[f"std_{a}"] = float(w[:, i].mean()), float(w[:, i].std())
    return f


def post_fall_inactive(after: np.ndarray, fs: int = 200, cfg: dict | None = None) -> tuple[bool, float]:
    """True if SVM std stays below threshold for >= N seconds. `after` = (n,3) g samples following the impact."""
    cfg = cfg or load_config()["agent3"]["inactivity"]
    n, s = int(cfg["seconds"] * fs), sf.svm(after)
    if len(s) < n:
        return False, len(s) / fs
    return bool(s[:n].std() < cfg["std_g"]), len(s) / fs


def build_windows(force: bool = False) -> pd.DataFrame:
    if CACHE.exists() and not force:
        return pd.read_parquet(CACHE)
    cfg = load_config()["agent3"]
    fs = cfg["fs"]
    n, stride = int(cfg["window_s"] * fs), int(cfg["stride_s"] * fs)
    rows = []
    for r in sf.list_files().itertuples():
        a = sf.load_file(r.path)
        base = {"source": "sisfall", "subject": r.subject, "group": r.group, "activity": r.activity, "file": r.path.split("/")[-1]}
        if r.is_fall:
            w = sf.fall_window(a, n)
            if w is None:
                continue
            pk = int(np.argmax(sf.svm(a)))
            ok, secs = post_fall_inactive(a[pk + int(1.0 * fs):], fs)  # check from 1 s after impact
            rows.append({**base, "y": 1, "win_start": pk - n // 2, "post_inactive": ok, **window_features(w, fs)})
        else:
            for s, w in sf.sliding_windows(a, n, stride):
                ok, _ = post_fall_inactive(a[s + n // 2 + fs:], fs)
                rows.append({**base, "y": 0, "win_start": s, "post_inactive": ok, "adl_seconds": stride / fs, **window_features(w, fs)})
            rows[-1]["file_seconds"] = len(a) / fs
    df = pd.DataFrame(rows)
    CACHE.parent.mkdir(exist_ok=True, parents=True)
    df.to_parquet(CACHE)
    return df


CACHE_V2 = ROOT / "data" / "processed" / "sisfall_windows_v2.parquet"
MODEL_PATH_V2 = ROOT / "models" / "fall_v2.joblib"


def peak_window(a: np.ndarray, n: int, jitter: int):
    """Window of n samples centred on the trial's own max-SVM sample, shifted by `jitter` samples. Same code for falls and
    ADLs. Returns (window, peak_index_in_trial, window_start) or None if it does not fit inside the recording."""
    pk = int(np.argmax(sf.svm(a)))
    s = pk + jitter - n // 2
    return (a[s:s + n], pk, s) if s >= 0 and s + n <= len(a) else None


def build_windows_v2(force: bool = False) -> pd.DataFrame:
    """Unbiased windowing. kind='fall' (one window per fall trial) and kind='adl_peak' (one per ADL trial) are built
    IDENTICALLY (centred on own max-magnitude sample, same length, same +/-0.5 s jitter); kind='adl_slide' = sliding
    windows over the ADL trials (realistic false-alarm exposure). `peak_pos` (argmax position in window) is a diagnostic
    column, never a feature. post_inactive = SVM std < limit for N s starting 1 s after the window's OWN max sample."""
    if CACHE_V2.exists() and not force:
        return pd.read_parquet(CACHE_V2)
    cfg = load_config()
    a3, fs = cfg["agent3"], cfg["agent3"]["fs"]
    n, stride, jit = int(a3["window_s"] * fs), int(a3["stride_s"] * fs), int(a3["jitter_s"] * fs)
    rng, inact = np.random.RandomState(cfg["seed"]), a3["inactivity"]
    rows = []
    for r in sf.list_files().itertuples():
        a = sf.load_file(r.path)
        base = {"source": "sisfall", "subject": r.subject, "group": r.group, "activity": r.activity, "file": r.path.split("/")[-1]}

        def row(w, s, kind, y):
            pk_w = int(np.argmax(sf.svm(w)))
            ok, _ = post_fall_inactive(a[s + pk_w + fs:], fs, inact)
            return {**base, "y": y, "kind": kind, "win_start": s, "peak_pos": pk_w, "post_inactive": ok, **window_features(w, fs)}

        pw = peak_window(a, n, int(rng.randint(-jit, jit + 1)))   # jitter drawn for every trial: RNG stream identical per file
        if pw is not None:
            rows.append(row(pw[0], pw[2], "fall" if r.is_fall else "adl_peak", int(r.is_fall)))
        if not r.is_fall:
            first = len(rows)
            for s, w in sf.sliding_windows(a, n, stride):
                rows.append({**row(w, s, "adl_slide", 0), "adl_seconds": stride / fs})
            if len(rows) > first:
                rows[-1]["file_seconds"] = len(a) / fs
    df = pd.DataFrame(rows)
    CACHE_V2.parent.mkdir(exist_ok=True, parents=True)
    df.to_parquet(CACHE_V2)
    return df


def _rf(seed):
    return RandomForestClassifier(n_estimators=300, min_samples_leaf=3, class_weight="balanced_subsample",
                                  random_state=seed, n_jobs=2)


def _alert_events(df: pd.DataFrame, alert: np.ndarray) -> int:
    """Count runs of consecutive flagged windows within a file (one physical alert, not one per window)."""
    d = df.assign(a=alert).sort_values(["file", "win_start"])
    prev = d.groupby("file")["a"].shift(1, fill_value=False)
    return int((d.a & ~prev).sum())


def score_oof(df: pd.DataFrame, seed: int) -> np.ndarray:
    """Out-of-fold RF scores, subject-level GroupKFold (same folds for any table with the same subjects)."""
    oof = np.zeros(len(df))
    for tr, te in subject_folds(df, load_config()["agent3"]["n_folds"], seed):
        oof[te] = _rf(seed).fit(df.iloc[tr][FEATURES], df.y.values[tr]).predict_proba(df.iloc[te][FEATURES])[:, 1]
    return oof


def with_kind(df: pd.DataFrame) -> pd.DataFrame:
    """v1 tables have no `kind`: falls are 'fall', every ADL window is a sliding window."""
    return df if "kind" in df.columns else df.assign(kind=np.where(df.y == 1, "fall", "adl_slide"))


def metrics_at(df: pd.DataFrame, oof: np.ndarray, thr: float, confirm: bool = False) -> dict:
    """Alert = score >= thr (and post-fall inactivity if `confirm`). Sensitivity on fall windows, specificity on ADL windows
    built like the falls (adl_peak; None for v1 tables), false alarms/hour = runs of flagged sliding ADL windows per hour."""
    df = with_kind(df)
    alert = (oof >= thr) & (df.post_inactive.values if confirm else True)
    y, kind = df.y.values, df.kind.values
    peak, slide = kind == "adl_peak", kind == "adl_slide"
    hrs_all = df[slide].groupby("file")["file_seconds"].max().sum() / 3600
    res = {"threshold": thr, "confirm_inactivity": confirm, "n_fall_windows": int(y.sum()), "n_adl_peak_windows": int(peak.sum()),
           "n_adl_slide_windows": int(slide.sum()), "adl_hours": float(hrs_all), "groups": {}}
    sc = oof
    if peak.any():
        m = (kind == "fall") | peak
        res["pr_auc"] = float(average_precision_score(y[m], sc[m]))
        res["roc_auc"] = float(roc_auc_score(y[m], sc[m]))
        m2 = (kind == "fall") | slide
        res["pr_auc_vs_sliding"] = float(average_precision_score(y[m2], sc[m2]))
    else:
        res["pr_auc"] = float(average_precision_score(y, sc))
        res["roc_auc"] = float(roc_auc_score(y, sc))
        res["pr_auc_vs_sliding"] = res["pr_auc"]
    for g in ("adult", "elderly"):
        gm = (df.group == g).values
        fall, sl = gm & (y == 1), gm & slide
        hrs = df[sl].groupby("file")["file_seconds"].max().sum() / 3600
        res["groups"][g] = {
            "n_falls": int(fall.sum()), "fall_subjects": sorted(df[fall].subject.unique().tolist()),
            "sensitivity": float(alert[fall].mean()) if fall.any() else None,
            "n_adl_windows": int((gm & (peak | slide)).sum()), "adl_hours": float(hrs),
            "specificity_windows": float(1 - alert[gm & peak].mean()) if (gm & peak).any() else float(1 - alert[sl].mean()),
            "specificity_sliding_windows": float(1 - alert[sl].mean()),
            "false_alarms_per_hour": _alert_events(df[sl], alert[sl]) / hrs}
    res["overall_false_alarms_per_hour"] = _alert_events(df[slide], alert[slide]) / hrs_all
    return res


def evaluate() -> dict:
    """Subject-level OOF evaluation on the unbiased v2 windows + final model fit saved to models/fall_v2.joblib
    (models/fall.joblib, the v1 model, is kept untouched). Operating point comes from config agent3.operating."""
    cfg = load_config()
    seed, op = cfg["seed"], cfg["agent3"]["operating"]
    df = build_windows_v2().reset_index(drop=True)
    assert_real_source(df, "sisfall")
    oof = score_oof(df, seed)
    res = metrics_at(df, oof, op["threshold"], op["require_inactivity"])
    res["n_subjects"] = int(df.subject.nunique())
    slide = (df.kind == "adl_slide").values
    worst = df[slide & (oof >= op["threshold"])].groupby("activity").size().sort_values(ascending=False).head(3)
    res["top_false_alarm_activities"] = worst.to_dict()
    res["post_fall_inactivity"] = {"detected_in_falls": float(df[df.y == 1].post_inactive.mean()),
                                   "detected_in_adl_peak_windows": float(df[df.kind == "adl_peak"].post_inactive.mean()),
                                   "detected_in_adl_sliding_windows": float(df[slide].post_inactive.mean()),
                                   "note": "checked for N s starting 1 s after the window's own max sample"}
    final = _rf(seed).fit(df[FEATURES], df.y)
    MODEL_PATH_V2.parent.mkdir(exist_ok=True)
    joblib.dump({"model": final, "features": FEATURES, "threshold": op["threshold"], "windowing": "v2"}, MODEL_PATH_V2)
    res["notes"] = ["OOF predictions, subject-level GroupKFold(5), v2 windows (falls and ADL peak windows built identically, +/-0.5 s jitter)",
                    "false alarms/hour = runs of consecutive flagged SLIDING ADL windows / hours of ADL recordings; SisFall ADL trials are short vigorous "
                    "trials, so this overstates daily life",
                    "SisFall: only 1 elderly subject (SE06) performed falls -> elderly sensitivity rests on 5 trials x 15 types of one person",
                    "SisFall falls are scripted on mats by young volunteers; real-world performance will be lower"]
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(res, indent=2))
    return res


_cache: dict = {}


def use_model(path) -> None:
    """Load a specific model file (e.g. the held-out-subject replay model) for predict_window."""
    _cache.clear()
    _cache.update(joblib.load(path))


def predict_window(window: np.ndarray, fs: int = 200, following: np.ndarray | None = None,
                   patient_id: str = "unknown", day: date | None = None) -> AgentResult:
    """window: (n, 3) first accelerometer in g (use sisfall.load_file). `following`: optional (m, 3) g samples after
    the impact for the post-fall inactivity check; without it only the tail of the window is used."""
    if not _cache:
        use_model(MODEL_PATH)
    f = window_features(window, fs)
    prob = float(_cache["model"].predict_proba(pd.DataFrame([f])[_cache["features"]])[:, 1][0])
    pk = int(np.argmax(sf.svm(window)))
    after = following if following is not None else window[pk + int(0.25 * fs):]
    inactive, secs = post_fall_inactive(after, fs)
    ev = {"post_fall_inactive": inactive, "inactivity_observed_s": round(secs, 2), "peak_svm_g": f["peak_svm"],
          "jerk_max": f["jerk_max"], "post_impact_std": f["post_impact_std"], "threshold": _cache["threshold"]}
    return AgentResult(patient_id=patient_id, day=day or date.today(), agent=AGENT, score=prob,
                       flag=prob >= _cache["threshold"], evidence=ev, ts=datetime.utcnow())


if __name__ == "__main__":
    r = evaluate()
    print(f"fall windows={r['n_fall_windows']} ADL peak={r['n_adl_peak_windows']} slide={r['n_adl_slide_windows']} ADL hours={r['adl_hours']:.1f} "
          f"PR-AUC={r['pr_auc']:.3f} (vs sliding {r['pr_auc_vs_sliding']:.3f})")
    print(f"{'group':<9}{'falls':>6}{'sens':>7}{'spec':>7}{'FA/hr':>7}")
    for g, v in r["groups"].items():
        s = "n/a" if v["sensitivity"] is None else f"{v['sensitivity']:.3f}"
        print(f"{g:<9}{v['n_falls']:>6}{s:>7}{v['specificity_windows']:>7.3f}{v['false_alarms_per_hour']:>7.2f}")
