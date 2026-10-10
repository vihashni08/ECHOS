"""Read-only EDA / data-quality audit of TIHM. Writes reports/eda/*.png and reports/eda/DATA_QUALITY.md.
Observation window per patient = first to last day with any Activity or Physiology record.
Usage: PYTHONPATH=. python3 scripts/eda.py"""
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from echos.data import loaders as L

warnings.filterwarnings("ignore")
OUT = Path("reports/eda")
OUT.mkdir(parents=True, exist_ok=True)
RANGES = {"Body Temperature": (34, 42), "Skin Temperature": (20, 40), "Systolic blood pressure": (60, 250),
          "Diastolic blood pressure": (30, 150), "Heart rate": (30, 220), "Body weight": (30, 200), "Total body water": (30, 80)}
VITALS3 = ["Systolic blood pressure", "Diastolic blood pressure", "Heart rate"]
plt.rcParams.update({"figure.dpi": 110, "axes.spines.top": False, "axes.spines.right": False, "font.size": 9})

act, phy, lab = L.load_activity(), L.load_physiology(), L.load_labels()
md, head = [], []


def sec(t):
    md.append(f"\n## {t}\n")


def to_md(df):
    d = df.reset_index()
    rows = [[str(x) for x in r] for r in d.values.tolist()]
    return "\n".join(["| " + " | ".join(map(str, d.columns)) + " |", "|" + "---|" * len(d.columns)] + ["| " + " | ".join(r) + " |" for r in rows])


# ---------------------------------------------------------------- 1. coverage
span = pd.concat([act[["patient_id", "day"]], phy[["patient_id", "day"]]]).groupby("patient_id").day.agg(["min", "max"])
span["n_days"] = (span["max"] - span["min"]).dt.days + 1
pids = sorted(span.index)
days_all = pd.date_range(min(act.day.min(), phy.day.min()), max(act.day.max(), phy.day.max()))
act_days = act.groupby("patient_id").day.nunique()
cov = pd.DataFrame({"first_day": span["min"].dt.date, "last_day": span["max"].dt.date, "window_days": span.n_days,
                    "activity_days": act_days})
for d in list(RANGES) + ["O/E - muscle mass"]:
    cov[d] = phy[phy.device_type == d].groupby("patient_id").day.nunique()
cov = cov.fillna(0)
vit_days = phy[phy.device_type.isin(VITALS3)].groupby("patient_id").day.nunique()
cov["bp_hr_days"] = vit_days
cov["bp_hr_days"] = cov["bp_hr_days"].fillna(0)
cov["bp_hr_readings_per_patient_week"] = (phy[phy.device_type == "Systolic blood pressure"].groupby("patient_id").size()
                                          .reindex(cov.index).fillna(0) / (cov.window_days / 7)).round(2)
cov.to_csv(OUT / "coverage_per_patient.csv")
sec("1. Coverage")
md.append(f"- Patients: {len(pids)} in any file (Activity {act.patient_id.nunique()}, Physiology {phy.patient_id.nunique()}, "
          f"Labels {lab.patient_id.nunique()}). Global range {days_all[0].date()} to {days_all[-1].date()}.\n"
          f"- Window per patient: median {int(cov.window_days.median())} days (min {int(cov.window_days.min())}, max {int(cov.window_days.max())}).\n"
          f"- Activity days per patient: median {int(cov.activity_days.median())}; BP/HR-reading days: median {int(cov.bp_hr_days.median())}; "
          f"median {cov.bp_hr_readings_per_patient_week.median():.2f} BP readings per patient-week.\n"
          f"- Patients with no Activity: {int((cov.activity_days == 0).sum())}; with no BP/HR reading: {int((cov.bp_hr_days == 0).sum())}.\n\n"
          "Per-patient table: `coverage_per_patient.csv`. Days with data by device type (median over patients): "
          + ", ".join(f"{d} {int(cov[d].median())}" for d in list(RANGES) + ["O/E - muscle mass"]) + ".")
idx = {d: i for i, d in enumerate(days_all)}
HA, HV = np.zeros((len(pids), len(days_all))), np.zeros((len(pids), len(days_all)))
for i, p in enumerate(pids):
    for d in act[act.patient_id == p].day.unique():
        HA[i, idx[d]] = 1
    for d in phy[(phy.patient_id == p) & phy.device_type.isin(VITALS3)].day.unique():
        HV[i, idx[d]] = 1
    HA[i, :][(days_all < span.loc[p, "min"]) | (days_all > span.loc[p, "max"])] = np.nan
    HV[i, :][(days_all < span.loc[p, "min"]) | (days_all > span.loc[p, "max"])] = np.nan
order = np.argsort(-np.nan_to_num(HV).sum(1))
fig, ax = plt.subplots(1, 2, figsize=(13, 8), sharey=True)
for a, H, t in [(ax[0], HA, "has_activity"), (ax[1], HV, "has BP/HR reading")]:
    a.imshow(H[order], aspect="auto", cmap=matplotlib.colors.ListedColormap(["#e8e8e8", "#2563eb"]), interpolation="nearest")
    a.set_title(f"{t} (grey = no record, blank = outside window)")
    a.set_xticks(range(0, len(days_all), 14))
    a.set_xticklabels([days_all[i].strftime("%m-%d") for i in range(0, len(days_all), 14)], rotation=45)
ax[0].set_ylabel("patient (sorted by vitals coverage)")
plt.tight_layout(); plt.savefig(OUT / "heatmap_coverage.png"); plt.close()

# ---------------------------------------------------------------- 2. missingness + gaps
sec("2. Missingness of vitals")
rows = []
for p in pids:
    s = span.loc[p]
    cal = pd.date_range(s["min"], s["max"])
    has = pd.Series(cal.isin(phy[(phy.patient_id == p) & phy.device_type.isin(VITALS3)].day.unique()), index=cal)
    run = best = 0
    for v in has.values:
        run = 0 if v else run + 1
        best = max(best, run)
    rows.append({"patient_id": p, "window_days": len(cal), "days_with_reading": int(has.sum()),
                 "share_no_reading": 1 - has.mean(), "longest_gap_days": best})
mis = pd.DataFrame(rows).set_index("patient_id")
mis.to_csv(OUT / "missingness_per_patient.csv")
tot_no = 1 - mis.days_with_reading.sum() / mis.window_days.sum()
md.append(f"- Overall share of patient-days with no BP/HR reading: **{tot_no:.1%}** ({int(mis.window_days.sum() - mis.days_with_reading.sum())} of {int(mis.window_days.sum())}).\n"
          f"- Per patient: median {mis.share_no_reading.median():.1%}, IQR {mis.share_no_reading.quantile(.25):.1%}-{mis.share_no_reading.quantile(.75):.1%}; "
          f"{int((mis.share_no_reading > 0.9).sum())} patients miss >90% of days.\n"
          f"- Longest run without a reading: median {int(mis.longest_gap_days.median())} days, max {int(mis.longest_gap_days.max())}; "
          f"{int((mis.longest_gap_days > 7).sum())} patients have a gap > 7 days (the triage data-gap limit).")
fig, ax = plt.subplots(1, 2, figsize=(11, 3.5))
ax[0].hist(mis.share_no_reading, bins=20, color="#2563eb"); ax[0].set_title("Share of days with no BP/HR reading, per patient")
ax[1].hist(mis.longest_gap_days, bins=20, color="#d97706"); ax[1].set_title("Longest gap without a reading (days)")
plt.tight_layout(); plt.savefig(OUT / "missingness.png"); plt.close()

# ---------------------------------------------------------------- 3. sensor outages
sec("3. Activity sensor outages")
cnt = act.groupby(["patient_id", "day"]).size().rename("n").reset_index()
flags = []
for p in pids:
    s = span.loc[p]
    if p not in act_days.index:
        continue
    a = act[act.patient_id == p]
    cal = pd.date_range(a.day.min(), a.day.max())
    c = cnt[cnt.patient_id == p].set_index("day").n.reindex(cal).fillna(0)
    med = c[c > 0].median()
    flags.append(pd.DataFrame({"patient_id": p, "day": cal, "n": c.values, "zero": c.values == 0, "low": (c.values > 0) & (c.values < 0.05 * med)}))
fl = pd.concat(flags)
fl["outage"] = fl.zero | fl.low
n_pd = len(fl)
byday = fl.groupby("day").agg(monitored=("patient_id", "size"), outage=("outage", "sum"))
byday["frac"] = byday.outage / byday.monitored
sys_days = byday[(byday.frac >= 0.3) & (byday.monitored >= 5)]
pat_out = fl.groupby("patient_id").outage.sum()
md.append(f"- Patient-days between a patient's first and last activity day: {n_pd}; zero events: **{int(fl.zero.sum())}** ({fl.zero.mean():.1%}); "
          f"below 5% of the patient's median: **{int(fl.low.sum())}** ({fl.low.mean():.1%}).\n"
          f"- Days where >=30% of monitored patients are in outage (system-wide candidates): **{len(sys_days)}**"
          + (": " + ", ".join(str(d.date()) for d in sys_days.index[:10]) + (" ..." if len(sys_days) > 10 else "") if len(sys_days) else "") + ".\n"
          f"- Outage patient-days on those system-wide days: {int(sys_days.outage.sum())} of {int(fl.outage.sum())} ({sys_days.outage.sum() / max(fl.outage.sum(), 1):.0%}); "
          f"the rest ({int(fl.outage.sum() - sys_days.outage.sum())}) are patient-specific.\n"
          f"- Patients with any outage day: {int((pat_out > 0).sum())} of {len(pat_out)}; most affected: "
          + ", ".join(f"{p} ({int(v)})" for p, v in pat_out.sort_values(ascending=False).head(5).items()) + ".")
fig, ax = plt.subplots(figsize=(11, 3.5))
ax.bar(byday.index, byday.frac, color="#2563eb", width=1.0); ax.axhline(0.3, ls="--", c="#d97706")
ax.set_title("Share of monitored patients in an Activity outage, by day (dashed = 30% system-wide threshold)")
plt.tight_layout(); plt.savefig(OUT / "activity_outages.png"); plt.close()

# ---------------------------------------------------------------- 4. value validity
sec("4. Value validity (Physiology)")
vrows = []
for d, (lo, hi) in RANGES.items():
    v = phy[phy.device_type == d].value
    bad = ((v < lo) | (v > hi)).sum()
    vrows.append({"device_type": d, "n": len(v), "units": ",".join(map(str, phy[phy.device_type == d].unit.unique()[:3])),
                  "min": v.min(), "p50": v.median(), "max": v.max(), "range": f"{lo}-{hi}", "implausible": int(bad),
                  "implausible_%": round(100 * bad / len(v), 2), "nan": int(v.isna().sum())})
v = phy[phy.device_type == "O/E - muscle mass"].value
vrows.append({"device_type": "O/E - muscle mass", "n": len(v), "units": ",".join(map(str, phy[phy.device_type == 'O/E - muscle mass'].unit.unique()[:3])),
              "min": v.min(), "p50": v.median(), "max": v.max(), "range": "(none given)", "implausible": 0, "implausible_%": 0, "nan": int(v.isna().sum())})
vt = pd.DataFrame(vrows).round(2)
vt.to_csv(OUT / "value_validity.csv", index=False)
md.append(to_md(vt.set_index("device_type")))
dup_p, dup_a, dup_l = phy.duplicated().sum(), act.duplicated().sum(), lab.duplicated().sum()
dup_p_key = phy.duplicated(["patient_id", "date", "device_type"]).sum()
ooo = {}
for name, df in [("Physiology", phy), ("Activity", act), ("Labels", lab)]:
    ooo[name] = int((df.groupby("patient_id").date.diff() < pd.Timedelta(0)).sum())
md.append(f"\n- Exact duplicate rows: Physiology {dup_p}, Activity {dup_a}, Labels {dup_l}. Physiology rows sharing patient+timestamp+device but differing value: {dup_p_key - dup_p}.\n"
          f"- Out-of-order timestamps (row earlier than the previous row of the same patient, in file order): "
          + ", ".join(f"{k} {v}" for k, v in ooo.items()) + ".\n"
          f"- Implausible total: {int(vt.implausible.sum())} of {int(vt.n.sum())} readings ({100 * vt.implausible.sum() / vt.n.sum():.2f}%).")
fig, axes = plt.subplots(2, 4, figsize=(14, 6))
for a, d in zip(axes.ravel(), list(RANGES) + ["O/E - muscle mass"]):
    x = phy[phy.device_type == d].value
    a.hist(x.clip(x.quantile(.001), x.quantile(.999)), bins=50, color="#2563eb")
    if d in RANGES:
        for b in RANGES[d]:
            a.axvline(b, c="#d97706", ls="--")
    a.set_title(d, fontsize=9)
plt.suptitle("Value distributions (dashed = plausible range; clipped to 0.1-99.9 pctl for display)")
plt.tight_layout(); plt.savefig(OUT / "value_distributions.png"); plt.close()

# ---------------------------------------------------------------- 5. labels
sec("5. Labels")
lt = lab.groupby("type").agg(rows=("day", "size"), patients=("patient_id", "nunique"), label_days=("day", lambda s: 0))
ld = lab.drop_duplicates(["patient_id", "day", "type"]).groupby("type").size()
lt["patient_days"] = ld
lt = lt.drop(columns="label_days").sort_values("rows", ascending=False)
no_lab = sorted(set(pids) - set(lab.patient_id))
per_pat = lab.drop_duplicates(["patient_id", "day"]).groupby("patient_id").size().reindex(pids).fillna(0).astype(int)
md.append(to_md(lt))
md.append(f"\n- Labelled patient-days per patient: median {int(per_pat.median())}, max {int(per_pat.max())} ({per_pat.idxmax()}).\n"
          f"- Patients with **zero labels**: {len(no_lab)} of {len(pids)}" + (": " + ", ".join(no_lab) if no_lab else "") + ".")
from echos.data.patient_day import load_patient_day
pdy = load_patient_day()
pdy["vitals_alert"] = ((pdy.bp_alert + pdy.pulse_alert + pdy.water_alert) > 0).astype(int)
rd = pdy[pdy.has_reading]
act_pd = pdy[pdy.has_activity]
base = pd.DataFrame({"agitation_rate_activity_days": act_pd.groupby("patient_id").agitation.apply(lambda s: (s > 0).mean()),
                     "vitals_alert_rate_reading_days": rd.groupby("patient_id").vitals_alert.mean()})
base.round(4).to_csv(OUT / "label_base_rates_per_patient.csv")
ag_p = base.agitation_rate_activity_days
va_p = base.vitals_alert_rate_reading_days
md.append(f"- Base rates: agitation **{(act_pd.agitation > 0).mean():.2%}** of activity days ({int((act_pd.agitation > 0).sum())}/{len(act_pd)}), "
          f"{int((ag_p > 0).sum())} of {len(ag_p)} patients ever agitated; per patient (those >0) median {ag_p[ag_p > 0].median():.1%}, max {ag_p.max():.1%}.\n"
          f"- Vitals alert (BP/pulse/water) **{rd.vitals_alert.mean():.1%}** of reading days ({int(rd.vitals_alert.sum())}/{len(rd)}), "
          f"{int((va_p > 0).sum())} of {len(va_p)} patients ever alerted; per patient (those >0) median {va_p[va_p > 0].median():.1%}, max {va_p.max():.1%}.\n"
          f"- Label days with no matching Activity record: {int(((pdy.agitation > 0) & ~pdy.has_activity).sum())} agitation, "
          f"{int(((pdy.vitals_alert > 0) & ~pdy.has_reading).sum())} vitals-alert days with no BP/HR reading.")
fig, ax = plt.subplots(1, 3, figsize=(14, 3.6))
lt.rows.plot.barh(ax=ax[0], color="#2563eb"); ax[0].set_title("Label rows by type")
ax[1].hist(ag_p.dropna(), bins=20, color="#d97706"); ax[1].set_title("Agitation base rate per patient")
ax[2].hist(va_p.dropna(), bins=20, color="#2563eb"); ax[2].set_title("Vitals-alert base rate per patient")
plt.tight_layout(); plt.savefig(OUT / "labels.png"); plt.close()

# ---------------------------------------------------------------- 6. summary
sec("6. What the cleaning step must handle")
nbad = int(vt.implausible.sum())
md.append(f"1. **Sparse vitals:** {tot_no:.0%} of patient-days have no BP/HR reading and {int((mis.longest_gap_days > 7).sum())} patients have a gap > 7 days: "
          "no forward-filling across long gaps; keep `has_reading` and the data-gap rule.\n"
          f"2. **Activity outages:** {int(fl.zero.sum())} zero-event and {int(fl.low.sum())} abnormally-low-event patient-days inside patients' windows "
          f"({len(sys_days)} system-wide candidate days): mask these days from baselines instead of treating them as 'no movement'.\n"
          f"3. **Implausible values:** {nbad} readings outside the given ranges (see table): drop or flag before building baselines; check units first.\n"
          f"4. **Duplicates / ordering:** {dup_p} exact duplicate Physiology rows, {dup_a} Activity, {dup_l} Labels; {sum(ooo.values())} out-of-order rows: "
          "deduplicate and sort by (patient, timestamp) before any shift/rolling feature.\n"
          f"5. **Labels:** {len(no_lab)} patients with zero labels; agitation in only {int((ag_p > 0).sum())} patients: keep patient-level splits and report wide CIs; "
          "label days lacking sensor data cannot be scored.")
head += [f"patients {len(pids)}; median window {int(cov.window_days.median())} d; median {int(cov.activity_days.median())} activity days, {int(cov.bp_hr_days.median())} BP/HR days",
         f"no BP/HR reading on {tot_no:.1%} of patient-days; {int((mis.longest_gap_days > 7).sum())} patients have a gap > 7 days (max {int(mis.longest_gap_days.max())})",
         f"activity outages: {int(fl.zero.sum())} zero-event + {int(fl.low.sum())} low-event patient-days ({fl.outage.mean():.1%}); "
         f"{len(sys_days)} system-wide days, {int(fl.outage.sum() - sys_days.outage.sum())} patient-specific",
         f"implausible physiology values: {nbad}/{int(vt.n.sum())} ({100 * nbad / vt.n.sum():.2f}%)",
         f"exact duplicates P/A/L: {dup_p}/{dup_a}/{dup_l}; out-of-order rows P/A/L: {ooo['Physiology']}/{ooo['Activity']}/{ooo['Labels']}",
         f"labels: {len(lab)} rows, {lab.patient_id.nunique()} patients; zero-label patients: {len(no_lab)}",
         f"agitation base rate {(act_pd.agitation > 0).mean():.2%} ({int((ag_p > 0).sum())} patients); vitals-alert base rate {rd.vitals_alert.mean():.1%} ({int((va_p > 0).sum())} patients)"]
(OUT / "DATA_QUALITY.md").write_text("# TIHM data quality\n\nGenerated by `scripts/eda.py` (read-only). Figures: heatmap_coverage, missingness, activity_outages, value_distributions, labels.\n"
                                     "\n## Headline\n\n" + "\n".join(f"- {h}" for h in head) + "\n" + "\n".join(md) + "\n")
print("\n".join(head))
