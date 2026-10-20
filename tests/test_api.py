"""API tests: FastAPI TestClient on a small fixture database. No network, no real LLM / PubMed / SMTP; the runtime is emulated in threads."""
import json
import os
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from echos.alerts.dispatcher import AlertDispatcher
from echos.alerts.mailer import DryRunMailer
from echos.api import models as M
from echos.api import services as V
from echos.api.app import create_app
from echos.config import ROOT, load_config
from echos.orchestrator import state as S
from echos.sim.controller import SimController
from tests.test_weekly import NOPM, PID, WEEK, disp, world  # noqa: F401  (fixture reuse)

TO = {"caregiver": "care@example.test", "clinician": "doc@example.test"}
ADDRS = ["care@example.test", "doc@example.test", "caregiver@example.test", "clinician@example.test"]


def sisfall_fall():
    from echos.data import sisfall as sf
    f = sf.list_files()
    if f.empty:
        return None
    f = f[f.is_fall & f.subject.isin(load_config()["replay"]["holdout_subjects"])]
    return f.iloc[0] if len(f) else None


@pytest.fixture
def api(world, tmp_path, monkeypatch):
    eng, _, kw = world
    monkeypatch.setenv("ECHOS_SMTP_PASSWORD", "s3cr3t-smtp-pw-123")
    root = tmp_path / "root"
    (root / "reports/daily").mkdir(parents=True)
    (root / "reports/RESULTS.md").write_text("# R\n\n## Limitations\n\n- TIHM has no falls.\n- Wide confidence intervals.\n\n## Next\nx\n")
    (root / "reports/daily/pw_2019-05-07.md").write_text("# Daily report\n\n**Status: MONITOR**\n")
    ev = {"time": "14:02"}
    with eng.begin() as c:
        S.upsert(c, S.reports, ["patient_id", "day", "kind"], patient_id=PID, day="2019-05-07", kind="daily", mode="llm", path="reports/daily/pw_2019-05-07.md",
                 validation=S.j({"ok": True, "errors": []}), created_ts=1.0,
                 provenance=S.j({"model": "gemini-x", "mode": "llm", "cache_hit": False, "tools": [{"name": "get_patient_context", "args": {}, "result_summary": "ctx"}],
                                 "sources_retrieved": [{"id": "KB-7"}], "validator": {"ok": True}, "fallback_reason": None, "requests": 2, "tokens": {"prompt": 10, "output": 5}}))
        f = sisfall_fall()
        pl = {"file": f.path if f is not None else "/nonexistent.txt", "win_start": 0, "subject": f.subject if f is not None else "SA03", "activity": "F01", "injected": True}
        c.execute(S.event_log.insert().values(event_id=5, patient_id=PID, day="2019-05-09", ts="2019-05-09T14:02:11", kind="sensor", payload=S.j(pl)))
        S.insert_ignore(c, S.agent_output, event_id=5, agent_id="fall", patient_id=PID, day="2019-05-09", done_ts=1.0,
                        result=S.j({"score": 0.998, "evidence": {"post_fall_inactive": True, "inactivity_observed_s": 4.2, "peak_svm_g": 2.6, "threshold": 0.8}}))
        for role, a in TO.items():
            c.execute(S.outbox.insert().values(alert_id=f"{PID}|2019-05-09|fall|5", recipient_role=role, address=a, channel="dryrun", subject="[ECHOS URGENT] x", body="b", body_html="h",
                                               status="SENT", attempts=1, error=f"boom for {a}", event_time="2019-05-09T14:02:11", queued_time=1.0, sent_time=2.0))
        S.upsert(c, S.agent_heartbeat, ["agent_id"], agent_id="quality", ts=time.time(), last_event_id=3, status="running", pid=os.getpid())
    ctx = V.Ctx(eng=eng, root=root, cfg=load_config(), metrics_dir=ROOT / "reports/metrics", allow_kill=False, **kw,
                weekly_kwargs={"budget": None, "retriever": NOPM})
    ctx.cfg = {**ctx.cfg, "alerts": {**ctx.cfg["alerts"], "send_real": False}}
    return TestClient(create_app(ctx)), ctx


GETS = ["/patients", f"/patients/{PID}/summary?day=2019-05-07", f"/patients/{PID}/summary", f"/patients/{PID}/statuses?days=7", f"/patients/{PID}/vitals?from=2019-05-06&to=2019-05-12",
        f"/patients/{PID}/reports/daily?day=2019-05-07", "/alerts", "/outbox", "/falls", "/falls/5", "/sim/state",
        "/agents/health", "/system/quality", "/system/metrics", "/system/llm-budget", "/system/limitations", "/system/config"]


def test_every_endpoint_returns_200_and_validates(api):
    c, ctx = api
    for u in GETS:
        r = c.get("/api" + u)
        assert r.status_code == 200, (u, r.text[:200])
    assert M.Page[M.PatientOut].model_validate(c.get("/api/patients").json()).items[0].patient_id == PID
    m = M.SummaryOut.model_validate(c.get(f"/api/patients/{PID}/summary?day=2019-05-07").json())
    assert m.status == "MONITOR" and any("above this person's usual range" in x for x in m.reasons) and not any("probab" in x.lower() or "risk" in x.lower() for x in m.reasons)
    s = c.get(f"/api/patients/{PID}/summary?day=2019-05-10").json()
    assert s["status"] == "MONITOR" and s["reasons"] == []                       # a data-gap-only day carries no 'why it fired' sentence
    assert [d["status"] for d in c.get(f"/api/patients/{PID}/statuses?days=3").json()] == ["URGENT", "MONITOR", "NORMAL"]
    v = M.VitalsOut.model_validate(c.get(f"/api/patients/{PID}/vitals?from=2019-05-06&to=2019-05-12").json())
    assert v.series["sys"].baseline_established and any(p.flagged and p.value == 162 for p in v.series["sys"].points) and len(v.series["sys"].bands) == 7
    d = M.DailyReportOut.model_validate(c.get(f"/api/patients/{PID}/reports/daily?day=2019-05-07").json())
    assert d.provenance.model == "gemini-x" and d.provenance.tools[0]["name"] == "get_patient_context" and "Status: MONITOR" in d.text
    f = M.FallDetail.model_validate(c.get("/api/falls/5").json())
    assert f.probability_text == ">0.99" and f.peak_g == 2.6 and f.inactivity and f.inactivity_observed_s == 4.2 and f.alert.alert_id.endswith("|fall|5") and len(f.outbox) == 2
    assert f.injected and f.in_training_set_of_final_model is True
    if f.waveform:
        w = f.waveform
        assert len(w.t_s) == len(w.magnitude_g) <= 260 and w.magnitude_g[w.impact_index] == max(w.magnitude_g)
    assert f.outbox[0].body == "b" and c.get("/api/outbox").json()["items"][0]["body"] is None
    cfg = c.get("/api/system/config").json()
    assert cfg == {"demo": False, "allow_kill": False, "dry_run_mail": True, "llm_enabled": os.environ.get("ECHOS_REPORT_LLM") != "off"}
    assert c.get("/api/system/limitations").json()["text"].startswith("- TIHM has no falls")
    assert c.get("/openapi.json").status_code == 200 and c.get("/docs").status_code == 200


def test_not_found_and_validation_errors(api):
    c, _ = api
    for u in (f"/patients/nope/summary", "/patients/nope/statuses", "/patients/nope/vitals", "/patients/nope/reports/daily", "/patients/nope/reports/weekly",
              f"/patients/{PID}/summary?day=2000-01-01", f"/patients/{PID}/reports/daily?day=2000-01-01", f"/patients/{PID}/reports/weekly?week=2019-W19", "/falls/999999",
              f"/patients/{PID}/reports/weekly/pdf?week=2019-W19"):
        r = c.get("/api" + u)
        assert r.status_code == 404 and r.json()["error"]["status"] == 404, u
    assert c.post("/api/sim/fall", json={"patient_id": "nope"}).status_code == 404
    assert c.get("/api/patients?limit=0").status_code == 422 and c.get(f"/api/patients/{PID}/reports/weekly?week=garbage").status_code in (404, 422)
    assert c.post("/api/sim/control", json={"action": "fly"}).status_code == 422
    assert c.post("/api/sim/control", json={"action": "speed"}).status_code == 422


def test_pagination(api):
    c, _ = api
    r = c.get("/api/outbox?limit=1&offset=1").json()
    assert r["total"] == 2 and len(r["items"]) == 1 and r["offset"] == 1


def test_no_secrets_and_no_unmasked_addresses_anywhere(api):
    c, ctx = api
    c.post(f"/api/patients/{PID}/reports/weekly/send", json={})
    blob = ""
    for u in GETS + ["/outbox?limit=500", "/openapi.json"]:
        r = c.get(u if u.startswith("/openapi") else "/api" + u)
        blob += r.text
    blob += c.post(f"/api/patients/{PID}/reports/weekly/send", json={}).text
    assert "s3cr3t-smtp-pw-123" not in blob and "ECHOS_SMTP_PASSWORD" not in blob
    for a in ADDRS:
        assert a not in blob, a
    assert "ca***@example.test" in blob and "cl***@example.test" in blob and "do***@example.test" in blob            # masked form is what is returned


def test_weekly_json_pdf_and_send_respects_send_real_false_and_dedup(api):
    c, ctx = api
    r = c.post(f"/api/patients/{PID}/reports/weekly/send?week={WEEK}", json={})
    j = r.json()
    assert r.status_code == 200 and j["queued"] and not j["deduplicated"] and j["send_real"] is False and j["alert_id"] == f"weekly|{PID}|{WEEK}"
    assert [o["channel"] for o in j["outbox"]] == ["dryrun"] and j["outbox"][0]["status"] == "SENT" and j["outbox"][0]["address"] == "cl***@example.test"
    again = c.post(f"/api/patients/{PID}/reports/weekly/send?week={WEEK}", json={}).json()
    assert again["deduplicated"] and not again["queued"] and len(again["outbox"]) == 1
    forced = c.post(f"/api/patients/{PID}/reports/weekly/send?week={WEEK}", json={"force": True}).json()
    assert forced["queued"] and forced["alert_id"].endswith("resend-1") and len(forced["outbox"]) == 2
    w = M.WeeklyReportOut.model_validate(c.get(f"/api/patients/{PID}/reports/weekly?week={WEEK}").json())
    assert w.pdf_available and w.narrative and w.data["status_counts"]["URGENT"] == 1 and w.pdf_pages <= 3
    p = c.get(f"/api/patients/{PID}/reports/weekly/pdf?week={WEEK}")
    assert p.status_code == 200 and p.headers["content-type"] == "application/pdf" and p.content[:4] == b"%PDF"


def test_kill_is_refused_when_disabled_and_reports_recovery_when_enabled(api):
    c, ctx = api
    r = c.post("/api/agents/quality/kill")
    assert r.status_code == 403 and "disabled" in r.json()["error"]["message"]
    ctx.allow_kill = True
    assert c.post("/api/agents/nope/kill").status_code == 404
    proc = subprocess.Popen(["sleep", "100"])
    with ctx.eng.begin() as cn:
        S.upsert(cn, S.agent_heartbeat, ["agent_id"], agent_id="quality", ts=time.time(), last_event_id=1, status="running", pid=proc.pid)

    def supervisor():                   # emulates the supervisor restarting the worker with a new pid
        proc.wait(10)
        time.sleep(0.6)
        with ctx.eng.begin() as cn:
            S.upsert(cn, S.agent_heartbeat, ["agent_id"], agent_id="quality", ts=time.time(), last_event_id=1, status="running", pid=proc.pid + 1)
    threading.Thread(target=supervisor, daemon=True).start()
    j = c.post("/api/agents/quality/kill?wait_s=15").json()
    assert j["recovered"] and j["killed_pid"] == proc.pid and 0 < j["recovery_s"] < 10 and proc.poll() is not None


def test_system_metrics_equal_the_json_files(api):
    c, ctx = api
    j = c.get("/api/system/metrics").json()
    d = ROOT / "reports/metrics"
    for name in ("model_selection", "calibration", "fall_audit"):
        assert j["files"][name] == json.loads((d / f"{name}.json").read_text())
    assert "fall_elderly_by_threshold" in j["files"]["model_selection"] and "model_selection" in j["available"]
    assert c.get("/api/system/metrics?names=agent3").json()["files"]["agent3"] == json.loads((d / "agent3.json").read_text())


def test_llm_budget_reads_the_call_log(api):
    from echos.reports.llm.llm_budget import Cache
    c, ctx = api
    cache = Cache(ctx.root / "data/llm_cache.sqlite")
    lc = ctx.cfg["llm"]
    for ev, st in (("call", 200), ("call", 200), ("cache_hit", "-"), ("call_failed", 429), ("cooldown_start", 429)):
        cache.log(ev, lc["model"], st, "x")
    j = c.get("/api/system/llm-budget").json()
    m = j["models"][0]
    assert m["model"] == lc["model"] and m["requests_last_minute"] == 3 and m["failures_last_24h"] == 1 and 0 < m["cooldown_remaining_s"] <= 60
    assert j["cache_hits"] == 1 and j["model_requests"] == 3 and j["cache_hit_rate"] == 0.25 and m["effective_rpm_cap"] == int(lc["rpm"]["primary"] * 0.8)


def test_cors_only_for_the_local_ui(api):
    c, _ = api
    ok = c.get("/api/patients", headers={"Origin": "http://localhost:5173"})
    bad = c.get("/api/patients", headers={"Origin": "http://evil.example"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173" and "access-control-allow-origin" not in bad.headers


# ------------------------------------------------------------------ /sim/fall end to end with an emulated runtime
@pytest.fixture
def live(tmp_path, monkeypatch):
    from tests.test_orchestrator import R, health, qual
    from echos.orchestrator.pipeline import build_pipeline, run_orchestrator
    eng = S.get_engine(tmp_path / "live.db")
    adl = {"file": "x", "win_start": 0, "subject": "SA03", "activity": "D01"}
    events = []
    for d in ("2019-05-06", "2019-05-07"):
        events += [{"patient_id": "p1", "day": d, "ts": f"{d}T10:00:00", "kind": "sensor", "payload": adl, "_fall": False},
                   {"patient_id": "p1", "day": d, "ts": f"{d}T23:59:59", "kind": "day", "payload": {}, "_fall": False}]
    ctl = SimController(eng, events=events)
    disp_ = AlertDispatcher(eng, DryRunMailer(), to=TO, backoff_s=[0.01])
    g = build_pipeline(eng, report_llm=None, report_root=tmp_path, gate=True, dispatcher=disp_, weekly_pdf=False)
    stop = threading.Event()

    def agents():                       # emulates the three worker processes
        while not stop.wait(0.05):
            with eng.begin() as c:
                evs = c.execute(select(S.event_log)).fetchall()
                done = {(r.event_id, r.agent_id) for r in c.execute(select(S.agent_output.c.event_id, S.agent_output.c.agent_id))}
                for e in evs:
                    outs = {"day": {"quality": qual(), "health_monitoring": health()},
                            "sensor": {"fall": R(0.97, post_fall_inactive=True, peak_svm_g=2.6) if json.loads(e.payload).get("injected") else R(0.01)}}[e.kind]
                    for a, res in outs.items():
                        if (e.event_id, a) not in done:
                            S.insert_ignore(c, S.agent_output, event_id=e.event_id, agent_id=a, patient_id=e.patient_id, day=e.day, result=S.j(res), done_ts=1.0)
    threads = [threading.Thread(target=agents, daemon=True), threading.Thread(target=ctl.run, daemon=True),
               threading.Thread(target=run_orchestrator, args=(eng, g), kwargs={"stop": stop, "poll_s": 0.05}, daemon=True)]
    for t in threads:
        t.start()
    ctx = V.Ctx(eng=eng, root=tmp_path, cfg=load_config(), pdy=__import__("pandas").DataFrame(columns=["patient_id", "day"]), demo=__import__("pandas").DataFrame(columns=["patient_id", "age_band", "sex"]))
    app = create_app(ctx)
    import socket as _s
    import uvicorn
    sock = _s.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()          # real server: TestClient buffers streaming bodies, SSE needs a live socket
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    tc = TestClient(app)
    tc.base_sse = f"http://127.0.0.1:{port}"
    yield tc, eng, ctl
    server.should_exit = True
    stop.set()
    ctl.stop.set()
    disp_.close(2)
    time.sleep(0.2)


@pytest.mark.skipif(sisfall_fall() is None, reason="SisFall recordings not on this machine")
def test_sim_fall_creates_alert_two_dryrun_rows_and_sse_event(live):
    c, eng, ctl = live
    assert c.get("/api/patients").json()["items"][0]["patient_id"] == "p1"
    lines: list = []
    t_open = time.time()

    def reader():
        import httpx
        with httpx.stream("GET", c.base_sse + "/api/events?max_seconds=12", timeout=20) as r:
            for line in r.iter_lines():
                lines.append((time.time(), line))
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    time.sleep(0.5)
    r = c.post("/api/sim/fall", json={"patient_id": "p1", "subject": "SA03"})
    j = r.json()
    assert r.status_code == 200 and j["status"] == "DONE" and j["sisfall_subject"] == "SA03" and j["day"] == "2019-05-06"
    assert j["in_training_set_of_worker_model"] is False and j["in_training_set_of_final_model"] is True and "SYNTHETIC" in j["note"]
    t_alert = None
    for _ in range(100):
        with eng.begin() as cn:
            al = cn.execute(select(S.alerts)).fetchall()
            ob = cn.execute(select(S.outbox)).fetchall()
        if al and len(ob) == 2:
            t_alert = time.time()
            break
        time.sleep(0.1)
    assert t_alert and al[0].type == "fall" and sorted(o.recipient_role for o in ob) == ["caregiver", "clinician"] and {o.channel for o in ob} == {"dryrun"}
    deadline = t_alert + 2
    while time.time() < deadline and not any(l.startswith("event: alert") for _, l in lines):
        time.sleep(0.05)
    seen = [(t, l) for t, l in lines if l.startswith("event: alert")]
    assert seen and seen[0][0] - t_alert < 2.0                                            # the event reaches /events within 2 s
    th.join(15)
    kinds = {l[7:] for _, l in lines if l.startswith("event: ")}
    assert {"alert", "day_status", "outbox_status"} <= kinds and any(l.startswith(": heartbeat") or l.startswith(": connected") for _, l in lines)
    f = c.get("/api/falls").json()
    assert f["total"] == 1 and f["items"][0]["alert_status"] in ("PENDING", "SENT") and f["items"][0]["sisfall_subject"] == "SA03"
    last_id = max(int(l[4:]) for _, l in lines if l.startswith("id: "))
    import httpx
    with httpx.stream("GET", c.base_sse + "/api/events?max_seconds=1", headers={"Last-Event-ID": str(last_id - 1)}, timeout=10) as r2:            # reconnect: replays what was missed
        got = [l for l in r2.iter_lines() if l.startswith("id: ")]
    assert got and int(got[0][4:]) == last_id


def test_sim_control_commands_and_errors(live):
    c, eng, ctl = live
    assert c.get("/api/sim/state").json()["status"] == "idle"
    assert c.post("/api/sim/control", json={"action": "speed", "speed": 400}).json()["speed"] == 400
    assert c.post("/api/sim/control", json={"action": "speed", "speed": 0}).status_code == 409        # out of range -> controller error -> 409
    assert c.post("/api/sim/control", json={"action": "play"}).json()["status"] in ("playing", "finished")
    for _ in range(60):
        if c.get("/api/sim/state").json()["status"] == "finished":
            break
        time.sleep(0.1)
    st = c.get("/api/sim/state").json()
    assert st["status"] == "finished" and st["cursor"] == 4 and st["runtime_alive"]
    assert c.post("/api/sim/control", json={"action": "play"}).status_code == 409                     # finished: reset first
    assert c.post("/api/sim/control", json={"action": "reset"}).json()["status"] == "idle"
    with eng.begin() as cn:
        assert cn.execute(select(S.event_log)).first() is None


def test_api_process_never_imports_torch_or_xgboost(tmp_path):
    code = ("import os, sys; os.environ['ECHOS_API_NO_AUTOSTART']='1'\n"
            "from fastapi.testclient import TestClient\n"
            "from echos.api.app import create_app\n"
            "from echos.api import services as V\n"
            "from echos.orchestrator import state as S\n"
            f"eng = S.get_engine(r'{tmp_path / 'p.db'}')\n"
            "c = TestClient(create_app(V.Ctx(eng=eng, allow_kill=False)))\n"
            "for u in ('/api/patients','/api/falls','/api/alerts','/api/outbox','/api/agents/health','/api/sim/state','/api/system/quality','/api/system/metrics','/api/system/llm-budget'):\n"
            "    assert c.get(u).status_code == 200, u\n"
            "import echos.reports.weekly.weekly_report, echos.reports.daily.agent\n"
            "bad = [m for m in ('torch','xgboost','sentence_transformers','shap','lightgbm','tensorflow') if m in sys.modules]\n"
            "print('BAD', bad)\n")
    r = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, timeout=170)
    assert r.returncode == 0, r.stderr[-800:]
    assert "BAD []" in r.stdout



# ------------------------------------------------------------------ UI needs: structured daily report, cited sources, weekly build, outbox kind
def test_daily_report_is_structured_without_citation_tags_or_probabilities(api):
    c, ctx = api
    md = ("# Daily report for pw - 2019-05-07\n\n**Status: MONITOR** - Changes noticed\n\n## Alerts\n\n- Possible fall at 14:02 (likelihood 0.93); little movement afterwards.\n\n"
          "## What changed compared with their usual pattern\n\n- Systolic blood pressure 162 was above this person's usual range (usual 130). [F1]\n\n"
          "## What to monitor today\n\n- Repeat the blood pressure later today.\n- Keep drinking fluids.\n\n## Data note\nNo vitals reading for 2 days.\n\n---\n_Decision-support only._\n")
    (ctx.report_root / "reports/daily/pw_2019-05-07.md").write_text(md)
    d = M.DailyReportOut.model_validate(c.get(f"/api/patients/{PID}/reports/daily?day=2019-05-07").json())
    assert d.status == "MONITOR" and d.alerts[0].startswith("Possible fall at 14:02") and d.changes == ["Systolic blood pressure 162 was above this person's usual range (usual 130)."]
    assert d.monitor == ["Repeat the blood pressure later today.", "Keep drinking fluids."] and d.data_note == "No vitals reading for 2 days."


def test_sources_local_pubmed_and_unknown(api, monkeypatch):
    c, ctx = api
    long = " ".join(f"w{i}" for i in range(100))
    monkeypatch.setattr(V, "_chroma_get", lambda ctx_, sid: {"title": "Hypertension in adults", "publisher": "NICE NG136 (2019)", "page": "5", "text": long, "file": "x.pdf"} if sid == "KB-7" else None)
    k = M.SourceOut.model_validate(c.get("/api/sources/KB-7").json())
    assert (k.origin, k.title, k.source, k.year, k.page, k.url) == ("local", "Hypertension in adults", "NICE NG136 (2019)", "2019", "5", None) and len(k.excerpt.split()) <= 41 and k.excerpt.endswith("...")
    # PubMed: from the SQLite cache; never any abstract text
    import sqlite3
    from echos.knowledge.pubmed import PubMedClient
    pm = PubMedClient(http_get=lambda *a: b"", cache_path=ctx.root / "data/pubmed_cache.sqlite")
    cn = sqlite3.connect(pm.cache_path)
    cn.execute("INSERT INTO pubmed_cache VALUES (?,?,?,?)", ("k", "q", json.dumps({"sources": [{"id": "PM-12345678", "title": "Home BP in older adults", "journal": "Age and Ageing", "year": "2021",
                                                                                              "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/", "text": "SECRET ABSTRACT TEXT"}]}), 1.0))
    cn.commit(); cn.close()
    r = c.get("/api/sources/PM-12345678")
    j = r.json()
    assert (j["origin"], j["journal"], j["year"], j["url"], j["excerpt"]) == ("pubmed", "Age and Ageing", "2021", "https://pubmed.ncbi.nlm.nih.gov/12345678/", None) and "SECRET" not in r.text
    assert c.get("/api/sources/KB-999").status_code == 404 and c.get("/api/sources/XX-1").status_code == 404


def test_weekly_build_stores_a_report_without_emailing_and_outbox_has_kind_and_delivery(api):
    c, ctx = api
    r = c.post(f"/api/patients/{PID}/reports/weekly/build?week={WEEK}")
    assert r.status_code == 200 and r.json()["pdf_available"] and r.json()["week"] == WEEK
    assert c.get("/api/outbox?limit=500").json()["total"] == 2          # the two fixture fall rows only: building sends nothing
    c.post(f"/api/patients/{PID}/reports/weekly/send?week={WEEK}", json={})
    rows = c.get("/api/outbox?limit=500").json()["items"]
    wk = next(o for o in rows if o["kind"] == "weekly")
    assert wk["delivery"] == "dry_run" and wk["attachment_name"] == f"{PID}_{WEEK}.pdf" and wk["status"] == "SENT"
    assert {o["kind"] for o in rows} == {"alert", "weekly"}


def test_weekly_available_lists_stored_weeks_only(api):
    c, _ = api
    assert c.get(f"/api/patients/{PID}/reports/weekly-available").json() == []
    c.post(f"/api/patients/{PID}/reports/weekly/build?week={WEEK}")
    assert c.get(f"/api/patients/{PID}/reports/weekly-available").json() == [WEEK]
    assert c.get("/api/patients/nope/reports/weekly-available").status_code == 404


def test_outbox_retry_endpoint_resends_one_failed_row_but_never_a_sent_one(api):
    c, ctx = api
    with ctx.eng.begin() as cn:
        oid = cn.execute(S.outbox.insert().values(alert_id=f"{PID}|2019-05-09|fall|9", recipient_role="clinician", address=TO["clinician"], channel="dryrun", subject="[ECHOS URGENT] x",
                                                  body="b", body_html="h", status="FAILED", attempts=7, error="ConnectionError", event_time="2019-05-09T14:02:11",
                                                  queued_time=time.time() - 60, last_attempt_time=time.time() - 30)).inserted_primary_key[0]
        S.insert_ignore(cn, S.alerts, alert_id=f"{PID}|2019-05-09|fall|9", patient_id=PID, type="fall", time=1.0, status="FAILED", event_id=9, event_time="2019-05-09T14:02:11", payload="{}")
    row = next(x for x in c.get("/api/outbox?status=FAILED").json()["items"] if x["outbox_id"] == oid)
    assert row["can_retry"] and row["retry_window_end"] and row["sweep_until"] and row["last_attempt_time"]
    r = c.post(f"/api/outbox/{oid}/retry")
    assert r.status_code == 200 and r.json()["status"] == "SENT" and r.json()["delivery"] == "dry_run" and r.json()["attempts"] == 8     # DryRun: nothing leaves the machine
    assert c.post(f"/api/outbox/{oid}/retry").status_code == 409                                                                          # already sent: no duplicate
    assert c.post("/api/outbox/99999/retry").status_code == 404
