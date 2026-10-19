"""Fault injection with FAKES (no Gemini, no PubMed, no SMTP): 3 patients x 10 days, each case <= 3 min, compared with a clean run of the same stream.

Harness = the real runtime parts: Supervisor (3 worker processes), orchestrator thread, SimController (replays TIHM + held-out SisFall falls, 40% in it
a fall is INJECTED through the controller's inject_fall, exactly as POST /api/sim/fall does) and AlertDispatcher with a FakeMailer. The fault is fired
synchronously by the controller at the same event index in every run, so the clean run and the fault runs see the same event stream.
Cases: kill quality / health_monitoring / fall worker, kill the supervisor (alone, and together with a worker death), LLM raising errors, LLM timing out,
PubMed failing, SMTP failing then recovering (transient; outage longer than the retry window), API process restart mid-run.
Results are merged into reports/metrics/fault_injection.json (one entry per case) and the table in reports/RESULTS.md is rewritten from it.

Usage: PYTHONPATH=. python scripts/evaluation/fault_injection.py [--cases clean,kill_quality,...|all] [--table-only]   (each case ~1 min: run in the background)"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import warnings
from collections import Counter
from pathlib import Path
from unittest import mock

warnings.filterwarnings("ignore")
from echos.offline import force_offline  # noqa: E402

force_offline()   # this stage: no Gemini, no PubMed, no real SMTP
import numpy as np  # noqa: E402
from sqlalchemy import select  # noqa: E402

from echos.alerts.dispatcher import AlertDispatcher  # noqa: E402
from echos.alerts.mailer import FakeMailer  # noqa: E402
from echos.config import ROOT  # noqa: E402
from echos.data import sisfall as sf  # noqa: E402
from echos.orchestrator import state as S  # noqa: E402
from echos.orchestrator.pipeline import build_pipeline, run_orchestrator  # noqa: E402
from echos.orchestrator.supervisor import Supervisor  # noqa: E402
from echos.reports import agent as A  # noqa: E402
from echos.reports.fake_llm import FakeLLM  # noqa: E402
from echos.sim.controller import SimController  # noqa: E402
from echos.sim.replay import train_replay_fall_model  # noqa: E402
from scripts.evaluation.fault_report import CASES, OBSOLETE_HEADING, OLD_HEADING, LABEL, SMTP_OUTAGE_S, SUP_RESTART_DELAY_S, split_cases, table  # noqa: E402,F401

OUT = ROOT / "reports/metrics/fault_injection.json"
RESULTS = ROOT / "reports/RESULTS.md"
START, END = "<!-- fault_injection:start -->", "<!-- fault_injection:end -->"
PATIENTS, DAYS, CAP_S, TRIGGER_FRAC = 3, 10, 170, 0.4
EXTRA_FRACS = (0.5, 0.6, 0.7, 0.8, 0.9)   # further injected falls (steady-state latency samples; after the outage they show the recovered pipeline)
SPEED_EPS = 6.0
TO = {"caregiver": "care@example.test", "clinician": "doc@example.test"}
API_PORT = 8013
HB = 1.0
def pick_fall_file(model) -> dict:
    """A held-out-subject fall recording the replay model flags with inactivity (so a miss cannot be blamed on a fault)."""
    from echos.agents import fall as F
    from echos.config import load_config
    a3 = load_config()["agent3"]
    n, fs = int(a3["window_s"] * a3["fs"]), a3["fs"]
    F.use_model(model)
    files = sf.list_files()
    files = files[files.is_fall & files.subject.isin(load_config()["replay"]["holdout_subjects"]) & (files.group == "adult")]
    for r in files.itertuples():
        a = sf.load_file(r.path)
        pk = int(np.argmax(sf.svm(a)))
        s = pk - n // 2
        if s < 0 or s + n > len(a):
            continue
        w = a[s:s + n]
        res = F.predict_window(w, fs, following=a[s + int(np.argmax(sf.svm(w))) + fs:])
        if res.score >= 0.9 and res.evidence["post_fall_inactive"]:
            return {"file": r.path, "win_start": s, "subject": r.subject, "activity": r.activity}
    raise SystemExit("no suitable injection file")


def drop_rest_of_day(ctl: SimController, pid: str) -> None:
    """The injection closes the patient's open day now; its remaining scheduled sensor events would arrive after the day was processed (a race that makes
    runs differ), so they are dropped, identically in every run."""
    j = next((k for k in range(len(ctl.events)) if not ctl.done[k] and ctl.events[k]["patient_id"] == pid and ctl.events[k]["kind"] == "day"), None)
    if j is None:
        return
    day = ctl.events[j]["day"]
    for k in range(len(ctl.events)):
        if not ctl.done[k] and ctl.events[k]["patient_id"] == pid and ctl.events[k]["day"] == day and ctl.events[k]["kind"] != "day":
            ctl.done[k] = True


class HookedController(SimController):
    """Fires `on_trigger(self)` once, in the controller thread, when the cursor reaches `k` (same event index in every run), and `on_extra(self, i)` at the
    later indices in `extra_ks`."""
    def __init__(self, *a, k=None, on_trigger=None, extra_ks=(), on_extra=None, **kw):
        super().__init__(*a, **kw)
        self.k, self.on_trigger, self._fired = k, on_trigger, False
        self.extra_ks, self.on_extra, self._extra_done = list(extra_ks), on_extra, 0

    def emit_next(self):
        if self.k is not None and not self._fired and self.cursor >= self.k:
            self._fired = True
            self.on_trigger(self)
        while self._extra_done < len(self.extra_ks) and self.cursor >= self.extra_ks[self._extra_done]:
            self._extra_done += 1
            self.on_extra(self, self._extra_done)
        return super().emit_next()


class WindowMailer(FakeMailer):
    """FakeMailer that fails every send during the `outage_s` seconds after `t_down` is set."""
    def __init__(self, outage_s: float):
        super().__init__()
        self.outage_s, self.t_down = outage_s, None

    def send(self, to, subject, body, body_html=None, attachments=None):
        if self.t_down is not None and time.time() - self.t_down < self.outage_s:
            with self._lock:
                self.attempts.append((to, subject))
            raise ConnectionError("fake SMTP down")
        return super().send(to, subject, body, body_html, attachments)


class FlakyMailer(FakeMailer):
    """Every message fails its first `n_fail` attempts, then goes through (a transient SMTP problem hitting every alert)."""
    def __init__(self, n_fail: int = 2):
        super().__init__()
        self.n_fail, self._seen = n_fail, Counter()

    def send(self, to, subject, body, body_html=None, attachments=None):
        with self._lock:
            self._seen[(to, subject)] += 1
            n = self._seen[(to, subject)]
            self.attempts.append((to, subject))
        if n <= self.n_fail:
            raise ConnectionError("fake SMTP transient failure")
        with self._lock:
            self.sent.append({"to": to, "subject": subject, "body": body, "html": body_html, "t": time.time(), "attachments": []})


def fail_pubmed(url, params, timeout):
    from urllib.error import HTTPError
    raise HTTPError(url, 503, "fake unavailable", None, None)


def run_case(name: str, fall_pick: dict, model) -> dict:
    work = Path(tempfile.mkdtemp(prefix=f"fi_{name}_"))
    db = work / "echos.db"
    eng = S.get_engine(db)
    rec: dict = {"case": name, "label": LABEL[name]}
    extra_patches = []
    llm = None
    mailer: FakeMailer = FakeMailer()
    pm_calls = Counter()
    if name == "llm_errors":
        llm = FakeLLM([{"error": {"code": 503, "status": "UNAVAILABLE", "message": "fake outage"}}], model="fake-errors")
    elif name == "llm_timeout":
        llm = FakeLLM([lambda s, m, t: (time.sleep(2.0), {"error": "timeout"})[1]], model="fake-timeout")
    elif name == "pubmed_failing":
        llm = FakeLLM([lambda s, m, t: {"calls": [{"name": "retrieve_guidance", "args": {"finding_type": A.FINDING_TYPES[0]}}]} if t and len(m) == 1 else {"text": "{}"}],
                      model="fake-retrieving")
        from echos.knowledge.pubmed import PubMedClient

        def failing_http(url, params, timeout):
            pm_calls["http"] += 1
            return fail_pubmed(url, params, timeout)
        pmc = PubMedClient(http_get=failing_http, cache_path=work / "pm.sqlite", sleep=lambda s: None)
        extra_patches += [mock.patch.object(A, "_default_pubmed", lambda: pmc), mock.patch.object(A, "_safe_retrieve", lambda ft, d=None, k=None: []),
                          mock.patch.dict(os.environ, {"ECHOS_PUBMED": "on"})]
    elif name == "smtp_transient":
        mailer = FlakyMailer(2)
    elif name == "smtp_outage":
        mailer = WindowMailer(SMTP_OUTAGE_S)
    for p in extra_patches:
        p.start()
    sup_kw = dict(hb_s=HB, ck_s=1.0, stall_s=15, poll_s=0.5, missed=3, startup_grace_s=60, fall_model=model, worker_args={"fall": {"delay": 0.08}})
    sup = Supervisor(db, **sup_kw)
    sups = [sup]
    disp = AlertDispatcher(eng, mailer, to=TO)
    graph = build_pipeline(eng, report_llm=llm, dispatcher=disp, report_root=work / "files", weekly_pdf=False)
    timing: dict = {}
    injections: list = []
    api = {"proc": None}
    stop_orch = threading.Event()

    def kill_worker(agent: str):
        with eng.begin() as c:
            hb = c.execute(select(S.agent_heartbeat).where(S.agent_heartbeat.c.agent_id == agent)).first()
        timing["t_kill"] = time.time()
        os.kill(hb.pid, signal.SIGKILL)

    def monitor_recovery(agent: str, restart_via: str):
        t_kill = timing["t_kill"]
        end = time.time() + 90
        r = None
        while time.time() < end and r is None:
            time.sleep(0.1)
            with eng.begin() as c:
                r = c.execute(select(S.restart_log).where((S.restart_log.c.agent_id == agent) & (S.restart_log.c.detected_ts >= t_kill - 0.5))
                              .order_by(S.restart_log.c.id)).first()
        if r is None:
            timing["recovered"] = False
            return
        timing["detect_s"] = r.detected_ts - t_kill if restart_via == "supervisor" else None
        timing["restart_s"] = r.restarted_ts - t_kill
        while time.time() < end:
            time.sleep(0.1)
            with eng.begin() as c:
                n = c.execute(select(S.agent_output.c.done_ts).where((S.agent_output.c.agent_id == agent) & (S.agent_output.c.done_ts > r.restarted_ts))
                              .order_by(S.agent_output.c.done_ts).limit(1)).scalar()
            if n:
                timing["recover_s"], timing["recovered"] = n - t_kill, True
                return
        timing["recovered"] = False

    def restart_supervisor_later():
        time.sleep(SUP_RESTART_DELAY_S)
        s2 = Supervisor(db, **sup_kw)
        sups.append(s2)
        timing["supervisor_restarted_after_s"] = time.time() - timing["t_kill_sup"]
        for a in s2.agents:
            s2.started[a], s2.track[a] = S.now(), {"cursor": None, "since": S.now()}
            s2.restart(a, "supervisor_restarted", S.now())
        threading.Thread(target=s2.run, daemon=True).start()

    def on_trigger(ctl: HookedController):
        timing["trigger_cursor"] = ctl.cursor
        if name.startswith("kill_") and name not in ("kill_supervisor", "kill_supervisor_and_fall"):
            agent = name[len("kill_"):]
            kill_worker(agent)
            threading.Thread(target=monitor_recovery, args=(agent, "supervisor"), daemon=True).start()
        elif name in ("kill_supervisor", "kill_supervisor_and_fall"):
            sup.stop.set()                   # the supervisor process dies: no more heartbeat checks / restarts; workers are orphans that keep running
            timing["t_kill_sup"] = time.time()
            if name == "kill_supervisor_and_fall":
                kill_worker("fall")
                threading.Thread(target=restart_supervisor_later, daemon=True).start()
                threading.Thread(target=monitor_recovery, args=("fall", "operator"), daemon=True).start()
        elif name == "smtp_outage":
            mailer.t_down = time.time()
        elif name == "api_restart":
            timing["t_kill"] = time.time()
            api["proc"].kill()
            api["proc"].wait()
            threading.Thread(target=restart_api, daemon=True).start()
        t0 = time.time()
        drop_rest_of_day(ctl, ctl.events[0]["patient_id"])
        res = ctl.inject_fall({"patient_id": ctl.events[0]["patient_id"], **fall_pick})   # same path as POST /api/sim/fall
        timing["t_inject"], timing["inject_event_id"] = t0, res["event_id"]
        injections.append((res["event_id"], t0))

    def on_extra(ctl: HookedController, i: int):
        pid = sorted({e["patient_id"] for e in ctl.events})[i % PATIENTS]
        t0 = time.time()
        try:
            drop_rest_of_day(ctl, pid)
            res = ctl.inject_fall({"patient_id": pid, **fall_pick})
            injections.append((res["event_id"], t0))
        except ValueError:      # no open day left for that patient
            pass

    def start_api():
        env = {**os.environ, "ECHOS_DB": str(db), "ECHOS_API_PORT": str(API_PORT), "ECHOS_REPORT_LLM": "off", "ECHOS_DRY_RUN": "1", "ECHOS_PUBMED": "off",
               "ECHOS_REPORT_ROOT": str(work / "files"), "PYTHONPATH": str(ROOT), "ECHOS_CORS_ORIGIN": "http://localhost:5173"}
        api["proc"] = subprocess.Popen([sys.executable, "-W", "ignore", "-m", "echos.api"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < 60:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/api/sim/state", timeout=2).read()
                return time.time() - t0
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        raise RuntimeError("API did not start")

    def restart_api():
        time.sleep(2.0)
        t_restart = time.time()
        up = start_api()
        timing["api_downtime_s"] = time.time() - timing["t_kill"]
        timing["api_startup_s"] = up
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/api/events", timeout=5)
            timing["sse_reconnect_ok"] = r.status == 200
            r.close()
        except Exception as ex:  # noqa: BLE001
            timing["sse_reconnect_ok"] = f"{type(ex).__name__}"

    t_start = time.time()
    ctl = HookedController(eng, speed_eps=SPEED_EPS, n_patients=PATIENTS, max_days=DAYS, k=None, on_trigger=on_trigger, on_extra=on_extra)
    ctl._ensure_events()
    ctl.k = int(TRIGGER_FRAC * len(ctl.events))
    ctl.extra_ks = [int(f * len(ctl.events)) for f in EXTRA_FRACS]
    rec["events"] = len(ctl.events)
    rec["injected_fall"] = {"subject": fall_pick["subject"], "activity": fall_pick["activity"]}
    try:
        sup.start_all()
        threading.Thread(target=sup.run, daemon=True).start()
        end = time.time() + 90      # wait for the three workers
        while time.time() < end:
            with eng.begin() as c:
                up = [h for h in c.execute(select(S.agent_heartbeat)) if h.status == "running"]
            if len(up) == 3:
                break
            time.sleep(0.3)
        if name == "api_restart":
            start_api()
        orch = threading.Thread(target=lambda: run_orchestrator(eng, graph, stop=stop_orch, until_done=lambda: ctl.status == "finished", timeout=CAP_S), daemon=True)
        orch.start()
        threading.Thread(target=ctl.run, daemon=True).start()
        with eng.begin() as c:
            c.execute(S.sim_command.insert().values(ts=time.time(), cmd="play", args="{}", status="NEW"))
        orch.join(CAP_S + 5)
        rec["finished_within_cap"] = not orch.is_alive()
        disp.flush(60)
        A.drain_all(60)
        if name == "api_restart" and api["proc"]:
            try:
                al = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/api/alerts?limit=100", timeout=5).read())
                rec["api_alerts_visible_after_restart"] = len(al.get("items", al) if isinstance(al, dict) else al)
            except Exception as ex:  # noqa: BLE001
                rec["api_alerts_visible_after_restart"] = f"error {type(ex).__name__}"
        rec["wall_s"] = round(time.time() - t_start, 1)
        with eng.begin() as c:
            al = c.execute(select(S.alerts)).fetchall()
            ob = c.execute(select(S.outbox)).fetchall()
            cases = {f"{r.patient_id}|{r.day}": [r.status, r.reasons] for r in c.execute(select(S.case_state))}
            reps = c.execute(select(S.reports.c.mode, S.reports.c.validation, S.reports.c.provenance)).fetchall()
            gt = {g.event_id for g in c.execute(select(S.gt_fall))}
        rec["alerts"] = sorted(a.alert_id for a in al)
        rec["alert_status"] = dict(Counter(a.status for a in al))
        rec["outbox_rows"], rec["outbox_status"] = len(ob), dict(Counter(o.status for o in ob))
        rec["outbox_duplicates"] = len(ob) - len({(o.alert_id, o.recipient_role) for o in ob})
        rec["mails_sent"], rec["mail_duplicates"] = len(mailer.sent), len(mailer.sent) - len({(m["to"], m["subject"]) for m in mailer.sent})
        rec["cases"] = cases
        rec["report_modes"] = dict(Counter(r.mode for r in reps))
        rec["fallbacks"] = dict(Counter(json.loads(r.validation).get("fallback_reason") or "-" for r in reps if json.loads(r.validation).get("fallback_reason")))
        rec["pubmed_failures_recorded"] = sum(1 for r in reps if json.loads(r.provenance or "{}").get("pubmed_failure"))
        rec["pubmed_http_attempts"] = pm_calls["http"]
        rec["llm_requests"] = getattr(llm, "calls", 0)
        iid = timing.get("inject_event_id")
        inj = next((a for a in al if a.event_id == iid), None)
        if inj:
            sent = [o.sent_time for o in ob if o.alert_id == inj.alert_id and o.sent_time]
            rec["injected_alert"] = {"queued_s": round(inj.time - timing["t_inject"], 2), "sent_s": round(max(sent) - timing["t_inject"], 2) if sent else None,
                                     "status": inj.status}
        else:
            rec["injected_alert"] = None
        samples = []
        for eid, t0 in injections:
            a_ = next((a for a in al if a.event_id == eid), None)
            if a_:
                sent_ = [o.sent_time for o in ob if o.alert_id == a_.alert_id and o.sent_time]
                samples.append({"queued_s": round(a_.time - t0, 2), "sent_s": round(max(sent_) - t0, 2) if sent_ else None, "status": a_.status})
            else:
                samples.append({"queued_s": None, "sent_s": None, "status": "NO ALERT"})
        rec["injected_alerts_all"] = samples
        rec["falls_alerted"] = f"{sum(1 for a in al if a.type == 'fall' and a.event_id in gt)}/{len(gt)}"
        delays = [o.sent_time - o.queued_time for o in ob if o.sent_time]
        rec["send_delay_after_queue_s"] = {"median": round(float(np.median(delays)), 2), "max": round(float(max(delays)), 2)} if delays else None
        rec["timing"] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in timing.items() if k not in ("t_kill", "t_inject", "t_kill_sup")}
    finally:
        stop_orch.set()
        for s in sups:
            s.stop_all()
        if api["proc"] and api["proc"].poll() is None:
            api["proc"].kill()
        disp.close(2)
        for p in extra_patches:
            p.stop()
        shutil.rmtree(work, ignore_errors=True)
    return rec


def write_docs(d: dict):
    OUT.write_text(json.dumps(d, indent=1, default=str))
    ref = d["cases"].get("clean")
    md = [START, f"## Fault injection with fakes (written by scripts/evaluation/fault_injection.py)", "",
          f"{PATIENTS} patients x {DAYS} days ({ref['events'] if ref else '?'} events), real supervisor + 3 worker processes + orchestrator + simulation controller, FakeMailer, FakeLLM, "
          f"no network. A fall recording from a held-out SisFall subject is injected through the controller (same path as the API) at {int(TRIGGER_FRAC * 100)}% of the stream, in every run; "
          "the fault is fired at the same event index. Each case is compared with the clean run (case 'clean').", "",
          table(d), "",
          "Notes: alerts are unique per (patient, day, type, event), so duplicates are impossible by construction (the columns check the outbox rows and the mails the fake mailer received). "
          "'Detect' = kill -> supervisor notices and restarts; 'Recover' = kill -> the worker's first new output. 'Delay added' = injected fall: (time from injection to alert queued / sent) minus the same in the clean run. "
          "Differences between cases and the clean run are allowed only in quality / degraded flags (middle column).", ""]
    extra = []
    for name in ("smtp_outage", "smtp_transient", "api_restart", "pubmed_failing", "llm_errors", "llm_timeout", "kill_supervisor", "kill_supervisor_and_fall"):
        r = d["cases"].get(name)
        if not r:
            continue
        t = r.get("timing", {})
        if name.startswith("smtp"):
            extra.append(f"- {r['label']}: outbox {r['outbox_status']}, alert status {r['alert_status']}; send delay after queueing median/max {r['send_delay_after_queue_s']}. "
                         + ("Rows that exhausted the 3 attempts stay FAILED (visible, not re-sent automatically)." if r["outbox_status"].get("FAILED") else "All recovered by retry."))
        elif name == "api_restart":
            extra.append(f"- API restart: down {t.get('api_downtime_s')} s (includes a 2 s restart delay and {t.get('api_startup_s')} s start-up), SSE reconnect {t.get('sse_reconnect_ok')}, "
                         f"alerts visible through the restarted API: {r.get('api_alerts_visible_after_restart')}; the run itself was unaffected.")
        elif name == "pubmed_failing":
            extra.append(f"- PubMed failing: {r['pubmed_http_attempts']} failed HTTP attempts across {r['llm_requests']} fake-LLM requests; each failure was logged and the retriever fell back to local sources only; "
                         f"reports ended as {r['report_modes']} (the fake model's answers do not validate, so they fall back to the template: {r['fallbacks']}); no alert was affected.")
        elif name in ("llm_errors", "llm_timeout"):
            extra.append(f"- {r['label']}: {r['llm_requests']} fake requests, report modes {r['report_modes']}, fallbacks {r['fallbacks']}.")
        else:
            extra.append(f"- {r['label']}: workers kept running without a supervisor; " + (f"fall worker killed, restarted by the new supervisor {t.get('restart_s')} s after the kill, first output {t.get('recover_s')} s after the kill." if name.endswith("fall") else "no worker died, so nothing needed restarting."))
    md += extra + ["", f"Supervisor death is a single point of failure for RESTARTS only: with no supervisor a dead worker stays dead until the supervisor is restarted (assumed {SUP_RESTART_DELAY_S:g} s here), "
                   "and a fall alert injected during that outage waits for it (see the delay-added column).", END]
    s = RESULTS.read_text()
    block = "\n".join(md)
    s = s[:s.index(START)] + block + s[s.index(END) + len(END):] if START in s else s.rstrip() + "\n\n" + block + "\n"
    if OLD_HEADING in s:
        s = s.replace(OLD_HEADING, OBSOLETE_HEADING)
    RESULTS.write_text(s)


def main():
    arg = next((a for a in sys.argv[1:] if not a.startswith("--")), "all")
    want = CASES if arg == "all" else arg.split(",")
    d = json.loads(OUT.read_text()) if OUT.exists() and "cases" in json.loads(OUT.read_text()) else {"cases": {}}
    if "--table-only" not in sys.argv:
        if "clean" not in d["cases"] and "clean" not in want:
            want = ["clean"] + want
        model = train_replay_fall_model()
        pick = pick_fall_file(model)
        for name in want:
            t0 = time.time()
            d["cases"][name] = run_case(name, pick, model)
            d["config"] = {"patients": PATIENTS, "days": DAYS, "cap_s": CAP_S, "trigger_fraction": TRIGGER_FRAC, "supervisor_restart_delay_s": SUP_RESTART_DELAY_S,
                           "smtp_outage_s": SMTP_OUTAGE_S, "mail": "FakeMailer", "llm": "FakeLLM", "pubmed": "fake http", "obsolete": ["fault_injection_pre_agent0.json (v1)"]}
            write_docs(d)
            r = d["cases"][name]
            print(f"{name}: {time.time() - t0:.0f}s alerts={len(r['alerts'])} falls={r['falls_alerted']} timing={r['timing']} inj={r['injected_alert']}", flush=True)
            if name == "clean":
                print("  clean injected samples:", r["injected_alerts_all"], flush=True)
    write_docs(d)
    print(table(d))


if __name__ == "__main__":
    main()
