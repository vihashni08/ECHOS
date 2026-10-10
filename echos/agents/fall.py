"""Agent 3 - Fall. Window classifier trained on SisFall only (subject-level splits)."""
from __future__ import annotations

import json
from datetime import date, datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from echos.config import ROOT, load_config
from echos.data import sisfall as sf
from echos.data.splits import subject_folds
from echos.schemas import AgentResult

AGENT = "fall"
MODEL_PATH = ROOT / "models" / "fall.joblib"
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


def post_fall_inactive(after: np.ndarray, fs: int = 200) -> tuple[bool, float]:
    """True if SVM std stays below threshold for >= N seconds. `after` = (n,3) g samples following the impact."""
    cfg = load_config()["agent3"]["inactivity"]
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
        base = {"subject": r.subject, "group": r.group, "activity": r.activity, "file": r.path.split("/")[-1]}
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


def _rf(seed):
    return RandomForestClassifier(n_estimators=300, min_samples_leaf=3, class_weight="balanced_subsample",
                                  random_state=seed, n_jobs=2)


def _alert_events(df: pd.DataFrame, alert: np.ndarray) -> int:
    """Count runs of consecutive flagged windows within a file (one physical alert, not one per window)."""
    d = df.assign(a=alert).sort_values(["file", "win_start"])
    prev = d.groupby("file")["a"].shift(1, fill_value=False)
    return int((d.a & ~prev).sum())


def evaluate() -> dict:
    cfg = load_config()
    seed = cfg["seed"]
    df = build_windows().reset_index(drop=True)
    y = df.y.values
    oof = np.zeros(len(df))
    for tr, te in subject_folds(df, cfg["agent3"]["n_folds"], seed):
        oof[te] = _rf(seed).fit(df.iloc[tr][FEATURES], y[tr]).predict_proba(df.iloc[te][FEATURES])[:, 1]
    thr = 0.5
    alert = oof >= thr
    adl_hours = df[df.y == 0].groupby("file")["file_seconds"].max().sum() / 3600
    res = {"n_fall_windows": int(y.sum()), "n_adl_windows": int((y == 0).sum()), "n_subjects": int(df.subject.nunique()),
           "adl_hours": float(adl_hours), "threshold": thr,
           "pr_auc": float(average_precision_score(y, oof)), "roc_auc": float(roc_auc_score(y, oof)), "groups": {}}
    for g in ["adult", "elderly"]:
        m = (df.group == g).values
        fall, adl = m & (y == 1), m & (y == 0)
        hrs = df[adl].groupby("file")["file_seconds"].max().sum() / 3600
        res["groups"][g] = {"n_falls": int(fall.sum()), "fall_subjects": sorted(df[fall].subject.unique().tolist()),
                            "sensitivity": float(alert[fall].mean()) if fall.any() else None,
                            "n_adl_windows": int(adl.sum()), "adl_hours": float(hrs),
                            "specificity_windows": float(1 - alert[adl].mean()),
                            "false_alarms_per_hour": _alert_events(df[adl], alert[adl]) / hrs}
    res["overall_false_alarms_per_hour"] = _alert_events(df[y == 0], alert[y == 0]) / adl_hours
    worst = df[(y == 0) & alert].groupby("activity").size().sort_values(ascending=False).head(3)
    res["top_false_alarm_activities"] = worst.to_dict()
    res["post_fall_inactivity"] = {"detected_in_falls": float(df[y == 1].post_inactive.mean()),
                                   "detected_in_adl_windows": float(df[y == 0].post_inactive.mean()),
                                   "note": "falls: checked from 1 s after impact for N s; ADL: same offset from window centre"}
    final = _rf(seed).fit(df[FEATURES], y)
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump({"model": final, "features": FEATURES, "threshold": thr}, MODEL_PATH)
    res["notes"] = ["OOF predictions, subject-level GroupKFold(5)",
                    "false alarms/hour = runs of consecutive flagged ADL windows / hours of ADL recordings",
                    "SisFall: only 1 elderly subject (SE06) performed falls -> elderly sensitivity rests on 5 trials x 15 types of one person",
                    "SisFall falls are scripted on mats by young volunteers; real-world performance will be lower"]
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(res, indent=2))
    return res


_cache: dict = {}


def predict_window(window: np.ndarray, fs: int = 200, following: np.ndarray | None = None,
                   patient_id: str = "unknown", day: date | None = None) -> AgentResult:
    """window: (n, 3) first accelerometer in g (use sisfall.load_file). `following`: optional (m, 3) g samples after
    the impact for the post-fall inactivity check; without it only the tail of the window is used."""
    if not _cache:
        _cache.update(joblib.load(MODEL_PATH))
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
    print(f"fall windows={r['n_fall_windows']} ADL windows={r['n_adl_windows']} ADL hours={r['adl_hours']:.1f} PR-AUC={r['pr_auc']:.3f} ROC-AUC={r['roc_auc']:.3f}")
    print(f"{'group':<9}{'falls':>6}{'sens':>7}{'spec':>7}{'FA/hr':>7}")
    for g, v in r["groups"].items():
        s = "n/a" if v["sensitivity"] is None else f"{v['sensitivity']:.3f}"
        print(f"{g:<9}{v['n_falls']:>6}{s:>7}{v['specificity_windows']:>7.3f}{v['false_alarms_per_hour']:>7.2f}")
    print(f"overall FA/hr={r['overall_false_alarms_per_hour']:.2f}; inactivity: falls {r['post_fall_inactivity']['detected_in_falls']:.2f} vs ADL {r['post_fall_inactivity']['detected_in_adl_windows']:.2f}")
