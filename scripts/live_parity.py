"""Parity check: the live path (detectors scored from raw events, echos/live) must give the same scores as the replay path (scored from the pre-built
patient-day table) for the same patient-days. Offline, no network. Writes reports/metrics/live_parity.json.
Usage: PYTHONPATH=. python scripts/live_parity.py [--patients 4] [--days 14] [--start 2019-05-13]"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("ECHOS_REPORT_LLM", "off"); os.environ.setdefault("ECHOS_PUBMED", "off"); os.environ.setdefault("ECHOS_DRY_RUN", "1")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

from echos.agents import health_monitoring as HM  # noqa: E402
from echos.alerts.dispatcher import AlertDispatcher  # noqa: E402
from echos.alerts.mailer import FakeMailer  # noqa: E402
from echos.config import ROOT  # noqa: E402
from echos.live.emulator import Emulator  # noqa: E402
from echos.live.processor import LiveProcessor  # noqa: E402
from echos.orchestrator import state as S  # noqa: E402
from echos.reports.agent import ReportAgent  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patients", type=int, default=4)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--start", default="2019-05-13")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp())
    eng = S.get_engine(tmp / "parity.db")
    disp = AlertDispatcher(eng, FakeMailer(), to={"caregiver": "a@x.test", "clinician": "b@x.test"})
    proc = LiveProcessor(eng, disp, ReportAgent(eng, None, root=tmp / "f", budget=None))
    emu = Emulator(eng, cfg={"speed": 24 * 3600 * 2})
    pats = emu.load(start_day=a.start, n_days=a.days, n_patients=a.patients)
    emu.status = "playing"
    t = 0.0
    emu.tick(wall=0.0)
    while emu.status != "finished":
        t += 1.0
        emu.tick(wall=t)
        while proc.step():
            pass
    # live scores of each CLOSED day, rebuilt exactly as the processor did
    from echos.agents.health import behaviour as bm, vitals as vm
    from echos.live import features as LF, store as LS
    rows = []
    for p in pats:
        closed = LS.closed_rows(eng, p)
        for _, r in closed.iterrows():
            d = r["day"]
            hist = LF.history_for(proc.parquet, closed, p, d)
            full = pd.concat([hist, pd.DataFrame([r.to_dict()])], ignore_index=True, sort=False)
            full["day"] = pd.to_datetime(full["day"])
            for col in ("has_activity", "has_reading", "sensor_gap"):
                full[col] = full[col].fillna(False).astype(bool)
            lv, _ = vm.predict_live(p, d, full)
            lb, _ = bm.predict_live(p, d, full)
            ref = HM.predict(p, d)
            rows.append({"patient": p, "day": str(d.date()), "vit_live": lv.score if "current_vs_baseline" in lv.evidence else None, "vit_ref": ref.vitals.probability if ref.vitals.available else None,
                         "beh_live": lb.score if "reason" not in lb.evidence else None, "beh_ref": ref.behaviour.probability if ref.behaviour.available else None})
    df = pd.DataFrame(rows)
    out = {"patient_days": int(len(df))}
    for k in ("vit", "beh"):
        both = df.dropna(subset=[f"{k}_live", f"{k}_ref"])
        diff = (both[f"{k}_live"] - both[f"{k}_ref"]).abs()
        out[k] = {"compared": int(len(both)), "max_abs_diff": float(diff.max()) if len(diff) else None, "mean_abs_diff": float(diff.mean()) if len(diff) else None,
                  "live_only": int(df[f"{k}_live"].notna().sum() - len(both)), "ref_only": int(df[f"{k}_ref"].notna().sum() - len(both)),
                  "days_differing_more_than_0.01": int((diff > 0.01).sum())}
    (ROOT / "reports/metrics/live_parity.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out))


if __name__ == "__main__":
    main()
