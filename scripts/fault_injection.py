"""Fault injection: kill each agent, delay the LLM, remove the LLM key. Each run is compared with an identical clean run.

Usage: PYTHONPATH=. python3 scripts/fault_injection.py     -> reports/metrics/fault_injection.json
Alerts are written with a UNIQUE(patient, day, level) key, so 'duplicates' = 0 is also enforced by the schema; the
real checks are lost alerts, extra alerts and case results that differ from the clean run.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import warnings
from collections import Counter
from pathlib import Path
from unittest import mock

warnings.filterwarnings("ignore")
from echos.config import load_config  # noqa: E402
from scripts.kill_test import run_pipeline  # noqa: E402

LLM_DELAY_S = 1.0


class SlowFailingLLM:
    """Stands in for a Gemini call that hangs for LLM_DELAY_S and then times out (-> validator retry -> template)."""
    def generate(self, system, user):
        time.sleep(LLM_DELAY_S)
        raise TimeoutError("simulated LLM timeout")


def compare(ref, run, scenario, wall, ref_wall) -> dict:
    ra, ka = ref["alerts"], run["alerts"]
    same = sum(run["cases"].get(k) == v for k, v in ref["cases"].items())
    t = run["timing"]
    return {"scenario": scenario, "cases": len(ref["cases"]), "alerts_ref": len(ra), "alerts_run": len(ka),
            "lost_alerts": len(set(ra) - set(ka)), "extra_alerts": len(set(ka) - set(ra)),
            "duplicate_alerts": len(ka) - len(set(ka)),
            "falls_alerted": f"{len(run['gt_falls'] & set(ka))}/{len(run['gt_falls'])}",
            "cases_identical_to_clean_run": f"{same}/{len(ref['cases'])}",
            "detect_s": t.get("detect_s"), "recover_s": t.get("recover_s"),
            "report_modes": dict(Counter(run["modes"])), "wall_s": round(wall, 1), "wall_overhead_s": round(wall - ref_wall, 1)}


def timed(work, **kw):
    work.mkdir(parents=True)
    t0 = time.time()
    r = run_pipeline(work, **kw)
    return r, time.time() - t0


def main():
    tmp = Path(tempfile.mkdtemp())
    rows = []
    try:
        ref, ref_wall = timed(tmp / "ref", kill_agent=None)
        for agent in ("physiological", "behaviour", "fall"):
            r, w = timed(tmp / f"kill_{agent}", kill_agent=agent)
            rows.append(compare(ref, r, f"kill {agent}", w, ref_wall))
        r, w = timed(tmp / "llm_delay", kill_agent=None, report_llm=SlowFailingLLM())
        rows.append(compare(ref, r, f"LLM delayed {LLM_DELAY_S:g}s then timeout", w, ref_wall))
        # no key: model configured, key absent -> make_llm() returns None -> template mode (no code path needs the key)
        cfg = {**load_config(), "llm": {**load_config()["llm"], "model": "gemini-configured-but-no-key"}}
        env = {k: v for k, v in os.environ.items() if k != "ECHOS_LLM_API_KEY"}
        from echos.reports import llm as L
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(L, "load_config", return_value=cfg), \
                mock.patch("dotenv.load_dotenv", return_value=False):
            assert L.make_llm() is None
            r, w = timed(tmp / "nokey", kill_agent=None, report_llm="auto")
        rows.append(compare(ref, r, "LLM key removed", w, ref_wall))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    Path("reports/metrics").mkdir(parents=True, exist_ok=True)
    Path("reports/metrics/fault_injection.json").write_text(json.dumps(
        {"config": {"patients": 3, "days_each": 15, "llm_delay_s": LLM_DELAY_S, "ref_wall_s": round(ref_wall, 1)}, "rows": rows}, indent=2))
    print(json.dumps(rows, indent=1)[:200], "...")


if __name__ == "__main__":
    main()
