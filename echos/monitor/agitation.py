"""Agitation detector: was there a confirmed agitation episode in this activity window?

TIHM's own agitation model ran about every 6 hours and the monitoring team confirmed episodes with the participant / carer (Palermo et al.
2023), so labels carry timestamps at the window ends 08:00, 12:00 and 18:00. ECHOS uses the same windows:
    night   18:00 -> 08:00 (14 h)    morning 08:00 -> 12:00    afternoon 12:00 -> 18:00
A label belongs to the latest window end at or before its timestamp.

Features per window w of patient i (all from room / door sensor events in that window):
    counts per location, total, events per hour, room-to-room transitions, door openings, longest quiet gap (min), hours with any event,
    short gaps (< 60 s between events = rapid movement), room entropy.
Personal baseline per time slot (the same slot on the previous 14 days, >= 4 of them, past only):
    z_w = (x_w - median_prev) / (IQR_prev + 1)
History (labels strictly before the window): confirmed episodes in the last 21 days, windows since the last episode.
Model: gradient-boosted trees (XGBoost), output calibrated with Platt scaling fitted on the training patients only."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.linear_model import LogisticRegression

from echos.config import load_config

LOCATIONS = ["Bathroom", "Bedroom", "Hallway", "Kitchen", "Lounge", "Fridge Door", "Front Door", "Back Door"]


def _cfg() -> dict:
    return load_config()["monitor"]["agitation"]


def window_end(ts: pd.Series, slots=(8, 12, 18)) -> pd.Series:
    """The end of the window an event falls in (events at or after 18:00 belong to the night window ending 08:00 next day)."""
    s0, s1, s2 = slots
    day = ts.dt.normalize()
    h = ts.dt.hour
    end = np.where(h < s0, day + pd.Timedelta(hours=s0),
                   np.where(h < s1, day + pd.Timedelta(hours=s1),
                            np.where(h < s2, day + pd.Timedelta(hours=s2), day + pd.Timedelta(days=1, hours=s0))))
    return pd.Series(pd.to_datetime(end), index=ts.index)


def label_window(ts: pd.Series, slots=(8, 12, 18)) -> pd.Series:
    """Latest window end at or before the label time."""
    s0, s1, s2 = slots
    day = ts.dt.normalize()
    h = ts.dt.hour
    end = np.where(h >= s2, day + pd.Timedelta(hours=s2),
                   np.where(h >= s1, day + pd.Timedelta(hours=s1),
                            np.where(h >= s0, day + pd.Timedelta(hours=s0), day - pd.Timedelta(days=1) + pd.Timedelta(hours=s2))))
    return pd.Series(pd.to_datetime(end), index=ts.index)


def _entropy(counts: np.ndarray) -> float:
    c = counts[counts > 0].astype(float)
    if c.sum() == 0:
        return 0.0
    p = c / c.sum()
    return float(-(p * np.log(p)).sum())


def window_features(act: pd.DataFrame) -> pd.DataFrame:
    """Raw activity events -> one row per (patient_id, window end) with the window's features. Windows with no events are filled in
    between a patient's first and last window (a silent window is information too)."""
    slots = tuple(_cfg()["slot_hours"])
    a = act.sort_values(["patient_id", "date"]).copy()
    a["w"] = window_end(a["date"], slots)
    a["gap_min"] = a.groupby("patient_id")["date"].diff().dt.total_seconds() / 60
    a["moved"] = a["location_name"] != a.groupby("patient_id")["location_name"].shift()
    k = ["patient_id", "w"]
    f = a.pivot_table(index=k, columns="location_name", values="date", aggfunc="count", fill_value=0)
    for loc in LOCATIONS:
        if loc not in f:
            f[loc] = 0
    f = f[LOCATIONS]
    f.columns = [c.lower().replace(" ", "_") for c in f.columns]
    g = a.groupby(k)
    f["total"] = f.sum(axis=1)
    f["transitions"] = a[a["moved"]].groupby(k).size()
    f["doors"] = f["front_door"] + f["back_door"]
    f["max_gap_min"] = g["gap_min"].max()
    f["short_gaps"] = a[a["gap_min"] < 1].groupby(k).size()
    f["active_hours"] = g["date"].apply(lambda s: s.dt.hour.nunique())
    f["room_entropy"] = f[[c.lower().replace(" ", "_") for c in LOCATIONS]].apply(lambda r: _entropy(r.values), axis=1)
    f = f.fillna(0).reset_index()
    rows = []
    for pid, gp in f.groupby("patient_id"):
        days = pd.date_range(gp["w"].min().normalize() - pd.Timedelta(days=1), gp["w"].max().normalize())
        ends = sorted({d + pd.Timedelta(hours=h) for d in days for h in slots})
        ends = [e for e in ends if gp["w"].min() <= e <= gp["w"].max()]
        rows.append(pd.DataFrame({"patient_id": pid, "w": ends}))
    f = pd.concat(rows).merge(f, on=k, how="left").fillna(0)
    f["slot"] = f["w"].dt.hour
    hours = np.where(f["slot"] == slots[0], 24 - slots[2] + slots[0], np.where(f["slot"] == slots[1], slots[1] - slots[0], slots[2] - slots[1]))
    f["events_per_hour"] = f["total"] / hours
    f["source"] = "tihm"
    return f.sort_values(k).reset_index(drop=True)


RAW = ["bathroom", "bedroom", "hallway", "kitchen", "lounge", "fridge_door", "front_door", "back_door", "total", "transitions", "doors",
       "max_gap_min", "short_gaps", "active_hours", "room_entropy", "events_per_hour"]


def add_personal_slot_z(f: pd.DataFrame, n: int | None = None, min_n: int | None = None) -> pd.DataFrame:
    """z = (x - median of the same slot on the previous n days) / (IQR + 1); past only. NaN until min_n earlier slots exist."""
    n = _cfg()["baseline_slots"] if n is None else n
    min_n = _cfg()["min_baseline_slots"] if min_n is None else min_n
    f = f.sort_values(["patient_id", "w"]).copy()
    g = f.groupby(["patient_id", "slot"])
    for c in RAW:
        past = g[c].shift(1)
        grp = [f["patient_id"], f["slot"]]
        med = past.groupby(grp).transform(lambda s: s.rolling(n, min_periods=min_n).median())
        q75 = past.groupby(grp).transform(lambda s: s.rolling(n, min_periods=min_n).quantile(0.75))
        q25 = past.groupby(grp).transform(lambda s: s.rolling(n, min_periods=min_n).quantile(0.25))
        f[f"{c}_z"] = (f[c] - med) / (q75 - q25 + 1)
    return f


def attach_labels(f: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    slots = tuple(_cfg()["slot_hours"])
    ag = labels[labels["type"] == "Agitation"].copy()
    ag["w"] = label_window(pd.to_datetime(ag["date"]), slots)
    f = f.drop(columns=["y"], errors="ignore").merge(ag[["patient_id", "w"]].drop_duplicates().assign(y=1), on=["patient_id", "w"], how="left")
    f["y"] = f["y"].fillna(0).astype(int)
    return f


def add_history(f: pd.DataFrame, days: int | None = None) -> pd.DataFrame:
    """Confirmed episodes strictly BEFORE this window: count in the last `days` days and windows since the last one (large if none)."""
    days = _cfg()["history_days"] if days is None else days
    per_day = len(_cfg()["slot_hours"])
    f = f.sort_values(["patient_id", "w"]).copy()
    g = f.groupby("patient_id")["y"]
    prev = g.shift(1).fillna(0)
    f["hist_episodes"] = prev.groupby(f["patient_id"]).transform(lambda s: s.rolling(days * per_day, min_periods=1).sum())
    since = []
    for _, s in prev.groupby(f["patient_id"]):
        c, out = 999, []
        for v in s.values:
            c = 0 if v else min(c + 1, 999)
            out.append(c)
        since.append(pd.Series(out, index=s.index))
    f["windows_since_episode"] = pd.concat(since)
    return f


def build_table(act: pd.DataFrame | None = None, labels: pd.DataFrame | None = None) -> pd.DataFrame:
    from echos.data import loaders
    act = loaders.load_activity() if act is None else act
    labels = loaders.load_labels() if labels is None else labels
    f = add_personal_slot_z(window_features(act))
    return add_history(attach_labels(f, labels))


def feature_sets() -> dict[str, list[str]]:
    z = [f"{c}_z" for c in RAW]
    slot = ["slot_8", "slot_12", "slot_18"]
    hist = ["hist_episodes", "windows_since_episode"]
    return {"slot": slot, "history": hist, "raw": RAW, "personal_z": z, "raw+z": RAW + z, "raw+z+slot": RAW + z + slot,
            "full": RAW + z + slot + hist}


def add_slot_dummies(f: pd.DataFrame) -> pd.DataFrame:
    for h in (8, 12, 18):
        f[f"slot_{h}"] = (f["slot"] == h).astype(int)
    return f


@dataclass
class AgitationModel:
    features: list[str]
    params: dict = field(default_factory=lambda: dict(n_estimators=300, max_depth=3, learning_rate=0.03, subsample=0.8,
                                                      colsample_bytree=0.8, min_child_weight=2, random_state=0, verbosity=0))
    model: object = None
    platt: object = None

    def fit(self, train: pd.DataFrame) -> "AgitationModel":
        from echos.data.guard import assert_real_source
        assert_real_source(train, "tihm")
        X, y = train[self.features].fillna(0).values, train["y"].values
        self.model = xgb.XGBClassifier(**self.params).fit(X, y)
        # Platt scaling on out-of-fold training scores (3 patient folds inside the training set) -> calibrated probabilities
        from sklearn.model_selection import GroupKFold
        oof = np.zeros(len(train))
        for tr, te in GroupKFold(3).split(X, y, train["patient_id"].values):
            oof[te] = xgb.XGBClassifier(**self.params).fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
        lo = np.log(np.clip(oof, 1e-6, 1 - 1e-6) / (1 - np.clip(oof, 1e-6, 1 - 1e-6)))
        self.platt = LogisticRegression().fit(lo.reshape(-1, 1), y)
        return self

    def raw_score(self, df: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(df[self.features].fillna(0).values)[:, 1]

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        s = np.clip(self.raw_score(df), 1e-6, 1 - 1e-6)
        return self.platt.predict_proba(np.log(s / (1 - s)).reshape(-1, 1))[:, 1]

    def top_reasons(self, row: pd.Series, k: int = 3) -> list[str]:
        """Plain-words reasons: the personal-z features furthest above the person's usual for this time slot."""
        zs = {c[:-2]: float(row.get(c, 0) or 0) for c in self.features if c.endswith("_z")}
        top = sorted(zs.items(), key=lambda kv: -kv[1])[:k]
        return [f"{name.replace('_', ' ')} {row.get(name, 0):.0f} (usual-adjusted z {z:+.1f})" for name, z in top if z > 1]
