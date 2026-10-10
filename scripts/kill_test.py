"""Kill an agent mid-run; verify zero lost / duplicate alerts and measure detect + recover time.

Compares a run in which the Fall agent is SIGKILLed mid-stream against an identical run with no kill.
Usage: PYTHONPATH=. python3 scripts/kill_test.py [agent]
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
from sqlalchemy import func, select  # noqa: E402

from echos.orchestrator import state as S  # noqa: E402
from echos.orchestrator.graph import build_graph, run_orchestrator  # noqa: E402
from echos.orchestrator.supervisor import Supervisor  # noqa: E402
from echos.sim.replay import build_events, replay, train_replay_fall_model  # noqa: E402

HB, CK, DELAY = 1.0, 1.0, 0.08


def run_pipeline(work: Path, kill_agent: str | None, n_patients=3, max_days=15, rate=20.0, report_llm="auto",
                 kill_frac=0.4) -> dict:
    db = work / "echos.db"
    eng = S.get_engine(db)
    model = train_replay_fall_model()
    sup = Supervisor(db, hb_s=HB, ck_s=CK, stall_s=15, poll_s=0.5, missed=3, startup_grace_s=60, fall_model=model,
                     worker_args={"fall": {"delay": DELAY}})
    sup.start_all()
    t = threading.Thread(target=sup.run, daemon=True)
    t.start()
    graph, conn = build_graph(eng, work / "cp.sqlite", report_llm=report_llm)
    rep_done = threading.Event()
    summary = {}

    def _rep():
        summary.update(replay(eng, n_patients, max_days=max_days, rate_eps=rate))
        rep_done.set()
    threading.Thread(target=_rep, daemon=True).start()
    orch = threading.Thread(target=lambda: run_orchestrator(eng, graph, until_done=rep_done.is_set, timeout=600), daemon=True)
    orch.start()
    timing = {}
    if kill_agent:
        kinds = S.AGENT_KINDS[kill_agent]
        expected = sum(e["kind"] in kinds for e in build_events(n_patients, max_days=max_days)[0])
        kill_at = max(1, int(kill_frac * expected))
        while True:  # wait until the agent is ~40% through the sensor events
            time.sleep(0.2)
            with eng.begin() as c:
                done = c.execute(select(func.count()).select_from(S.agent_output).where(S.agent_output.c.agent_id == kill_agent)).scalar()
                hb = c.execute(select(S.agent_heartbeat).where(S.agent_heartbeat.c.agent_id == kill_agent)).first()
            if done >= kill_at and hb:
                break
        t_kill = S.now()
        os.kill(hb.pid, signal.SIGKILL)
        timing["killed_at_outputs"] = done
        while True:
            time.sleep(0.1)
            with eng.begin() as c:
                r = c.execute(select(S.restart_log).where(S.restart_log.c.agent_id == kill_agent)).first()
            if r:
                break
        timing["detect_s"] = r.detected_ts - t_kill
        while True:  # recovered = first new output after the restart
            time.sleep(0.1)
            with eng.begin() as c:
                n = c.execute(select(func.min(S.agent_output.c.done_ts)).where(
                    (S.agent_output.c.agent_id == kill_agent) & (S.agent_output.c.done_ts > r.restarted_ts))).scalar()
            if n:
                break
        timing["recover_s"] = n - t_kill
        timing["restart_to_first_output_s"] = n - r.restarted_ts
    orch.join(timeout=600)
    from echos.reports.agent import drain_all
    drain_all(600)  # background LLM report upgrades
    sup.stop_all()
    with eng.begin() as c:
        alerts = c.execute(select(S.alerts.c.patient_id, S.alerts.c.day)).fetchall()
        n_cases = c.execute(select(func.count()).select_from(S.case_state)).scalar()
        n_ev = c.execute(select(func.count()).select_from(S.event_log)).scalar()
        outs = c.execute(select(func.count()).select_from(S.agent_output)).scalar()
        gt = {(r.patient_id, r.day) for r in c.execute(select(S.gt_fall))}
        cases = {(r.patient_id, r.day): (r.status, r.reasons) for r in c.execute(select(S.case_state))}
        modes = [r.mode for r in c.execute(select(S.reports.c.mode))]
        fallbacks = [json.loads(r.validation).get("fallback_reason") for r in c.execute(select(S.reports.c.validation))]
        alert_lat = [r.created_ts for r in c.execute(select(S.alerts.c.created_ts))]
    conn.close()
    return {"alerts": [tuple(a) for a in alerts], "n_cases": n_cases, "events": n_ev, "outputs": outs, "gt_falls": gt,
            "timing": timing, "replay": summary, "cases": cases, "modes": modes, "fallbacks": [f for f in fallbacks if f], "alert_ts": alert_lat,
            "t_end": S.now()}


if __name__ == "__main__":
    agent = sys.argv[1] if len(sys.argv) > 1 else "fall"
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "ref").mkdir()
        (tmp / "kill").mkdir()
        ref = run_pipeline(tmp / "ref", None)
        kil = run_pipeline(tmp / "kill", agent)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ra, ka = ref["alerts"], kil["alerts"]
    res = {"killed_agent": agent, "events": kil["events"], "outputs_ref/kill": [ref["outputs"], kil["outputs"]],
           "cases_ref/kill": [ref["n_cases"], kil["n_cases"]],
           "alerts_ref/kill": [len(ra), len(ka)], "lost_alerts": len(set(ra) - set(ka)), "extra_alerts": len(set(ka) - set(ra)),
           "duplicate_alerts": len(ka) - len(set(ka)),
           "injected_falls_alerted_(kill run)": f"{len(kil['gt_falls'] & set(ka))}/{len(kil['gt_falls'])}",
           **{k: round(v, 2) for k, v in kil["timing"].items()}}
    Path("reports/metrics").mkdir(parents=True, exist_ok=True)
    Path("reports/metrics/kill_test.json").write_text(json.dumps(res, indent=2))
    for k, v in res.items():
        print(f"{k}: {v}")
