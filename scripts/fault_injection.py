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
A0_SLOW = {"quality": {"delay": 0.5}}  # Agent 0 slowed so Agents 1-2 queue behind it (kill-while-waiting case)


class StubLLM:
    """Deterministic stand-in for Gemini: returns unusable text, so every report validates -> falls back to its template."""
    def generate(self, system, user):
        return "{}"


def split_cases(ref, run):
    """Classify each case vs the clean run: identical / differs only by a quality flag / other."""
    ident = qual = other = 0
    for k, (rs, rr) in ref["cases"].items():
        ns, nr = run["cases"].get(k, (None, None))
        if (ns, nr) == (rs, rr):
            ident += 1
            continue
        a, b = set(json.loads(rr or "[]")), set(json.loads(nr or "[]"))
        extra, missing = b - a, a - b
        if ns is not None and all("quality" in x.lower() for x in extra) and extra and (not missing or rs == "NORMAL"):
            qual += 1
        else:
            other += 1
    return ident, qual, other


def real_client_1s():
    """The real Gemini client (needs ECHOS_LLM_API_KEY) with the hard per-call timeout forced to LLM_DELAY_S seconds."""
    from echos.reports.llm import make_llm
    cfg = load_config()
    llm = make_llm({**cfg, "llm": {**cfg["llm"], "timeout_s": LLM_DELAY_S}})
    if llm is None:
        raise SystemExit("real LLM client unavailable (no llm.model or ECHOS_LLM_API_KEY): cannot run the delayed-LLM scenario")
    return llm


def compare(ref, run, scenario, wall, ref_wall) -> dict:
    ra, ka = ref["alerts"], run["alerts"]
    same = sum(run["cases"].get(k) == v for k, v in ref["cases"].items())
    t = run["timing"]
    return {"scenario": scenario, "cases": len(ref["cases"]), "alerts_ref": len(ra), "alerts_run": len(ka),
            "lost_alerts": len(set(ra) - set(ka)), "extra_alerts": len(set(ka) - set(ra)),
            "duplicate_alerts": len(ka) - len(set(ka)),
            "falls_alerted": f"{len(run['gt_falls'] & set(ka))}/{len(run['gt_falls'])}",
            "cases_identical_to_clean_run": f"{same}/{len(ref['cases'])}",
            "cases_differing_only_in_quality_flag": split_cases(ref, run)[1], "cases_differing_other": split_cases(ref, run)[2],
            "agents12_outputs_without_quality": t.get("agents12_outputs_without_quality"),
            "passthrough_after_s": t.get("passthrough_after_s"), "permanent_outage": bool(t.get("permanent_outage")),
            "detect_s": t.get("detect_s"), "recover_s": t.get("recover_s"),
            "report_modes": dict(Counter(run["modes"])), "fallbacks": len(run["fallbacks"]),
            "fallback_reasons": dict(Counter(run["fallbacks"])), "wall_s": round(wall, 1), "wall_overhead_s": round(wall - ref_wall, 1)}


def timed(work, **kw):
    work.mkdir(parents=True)
    t0 = time.time()
    r = run_pipeline(work, **kw)
    return r, time.time() - t0


def main():
    tmp = Path(tempfile.mkdtemp())
    rows = []
    try:
        stub = StubLLM()
        ref, ref_wall = timed(tmp / "ref", kill_agent=None, report_llm=stub)
        for agent in ("quality", "physiological", "behaviour", "fall"):
            r, w = timed(tmp / f"kill_{agent}", kill_agent=agent, report_llm=stub)
            rows.append(compare(ref, r, f"kill {agent}" + (" (Agent 0, supervisor restarts)" if agent == "quality" else ""), w, ref_wall))
        r, w = timed(tmp / "kill_q_perm", kill_agent="quality", no_restart=("quality",), report_llm=stub)
        rows.append(compare(ref, r, "kill Agent 0, no restart (permanent outage)", w, ref_wall))
        r, w = timed(tmp / "kill_q_wait", kill_agent="quality", no_restart=("quality",), worker_args=A0_SLOW, wait_blocked=True, report_llm=stub)
        rows.append(compare(ref, r, "kill Agent 0 while Agents 1-2 wait, no restart", w, ref_wall))
        r, w = timed(tmp / "real_llm", kill_agent="quality", report_llm="auto")
        rows.append(compare(ref, r, "REAL Gemini, kill Agent 0 (restart)", w, ref_wall))
        r, w = timed(tmp / "llm_delay", kill_agent=None, report_llm=real_client_1s())
        rows.append(compare(ref, r, f"real Gemini client, {LLM_DELAY_S:g}s timeout", w, ref_wall))
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
        {"config": {"pipeline": "with Agent 0", "stub_llm": "all rows except REAL Gemini / 1s-timeout rows", "patients": 3, "days_each": 15, "llm_timeout_s": LLM_DELAY_S, "ref_wall_s": round(ref_wall, 1)}, "rows": rows}, indent=2))
    print(json.dumps(rows, indent=1)[:200], "...")


if __name__ == "__main__":
    main()
