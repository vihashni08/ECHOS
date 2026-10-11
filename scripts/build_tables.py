"""Rebuild the training tables from raw data (TIHM patient-day, SisFall windows). Both builders write the `source` column
themselves; prints old vs new row/label counts. Usage: PYTHONPATH=. python scripts/build_tables.py [patient_day|sisfall]"""
import json
import sys

import pandas as pd

from echos.agents import fall
from echos.config import ROOT, load_config
from echos.data import patient_day as pdm
from echos.data.guard import assert_real_source

PD = ROOT / load_config()["paths"]["processed"]
LABELS = ["bp_alert", "pulse_alert", "water_alert", "agitation"]


def summary(df, kind):
    if kind == "patient_day":
        return {"rows": len(df), "patients": int(df.patient_id.nunique()), **{k: int(df[k].sum()) for k in LABELS},
                "has_reading": int(df.has_reading.sum()), "has_activity": int(df.has_activity.sum())}
    return {"rows": len(df), "subjects": int(df.subject.nunique()), "falls": int(df.y.sum()), "adl_windows": int((df.y == 0).sum())}


def main(which):
    out = {}
    if which in ("all", "patient_day"):
        old = summary(pd.read_parquet(PD), "patient_day")
        new_df = pdm.save_patient_day()
        assert_real_source(new_df, "tihm")
        out["patient_day"] = {"old": old, "new": summary(new_df, "patient_day")}
    if which in ("all", "sisfall"):
        old = summary(pd.read_parquet(fall.CACHE_V2), "sisfall")
        new_df = fall.build_windows_v2(force=True)
        assert_real_source(new_df, "sisfall")
        out["sisfall"] = {"old": old, "new": summary(new_df, "sisfall")}
    for k, v in out.items():
        print(k, "SAME" if v["old"] == v["new"] else "DIFFERENT")
        for f in v["new"]:
            print(f"  {f:<12} old {v['old'][f]:>8}  new {v['new'][f]:>8}")
    (ROOT / "reports/metrics/table_rebuild.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
