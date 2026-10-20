"""Safety invariants: rules decide, nothing else can lower or delay a status. No real LLM, no network."""
import json
import random
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select

from echos.config import load_config
from echos.data.guard import assert_real_source
from echos.orchestrator import state as S
from echos.reports.llm.fake_llm import FakeLLM
from echos.orchestrator.pipeline import build_pipeline, process_case
from echos.orchestrator.triage import triage
from tests.test_orchestrator import R, health, qual, vit

CFG = load_config()["triage"]
CV = CFG["critical_vitals"]
N = 300


def rnd_health(rng):
    return health(vit(rng.randint(60, 200), rng.randint(40, 130), rng.randint(30, 150), rng.random()),
                  degraded=rng.random() < 0.2)


def rnd_quality(rng):
    return qual(sensor_gap=rng.random() < 0.3, days_since_last_reading=rng.randint(0, 12), quality_checked=rng.random() < 0.9)


def test_confirmed_fall_forces_urgent_random():
    rng = random.Random(1)
    for _ in range(N):
        hi = CFG["fall_prob_high"]
        p = rng.uniform(hi, 1.0) if hi is not None and rng.random() < 0.5 else rng.uniform(CFG["fall_prob"], 1.0)
        inactive = hi is None or p < hi or rng.random() < 0.5   # below fall_prob_high (or when it is null) a fall counts only with inactivity
        r = {"quality": rnd_quality(rng), "health": rnd_health(rng) if rng.random() < 0.9 else None,
             "fall": R(p, post_fall_inactive=inactive)}
        d = triage(r, rng.randint(0, 12), CFG)
        assert d.status.value == "URGENT" and "fall" in d.urgent_types


def test_critical_vitals_force_urgent_random():
    rng = random.Random(2)
    for _ in range(N):
        which = rng.choice(["sys_high", "sys_low", "dia_high", "hr_high", "hr_low"])
        s, d_, h = rng.randint(100, 170), rng.randint(60, 100), rng.randint(50, 110)
        if which == "sys_high": s = rng.randint(CV["sys_high"], 260)
        if which == "sys_low": s = rng.randint(40, CV["sys_low"] - 1)
        if which == "dia_high": d_ = rng.randint(CV["dia_high"], 160)
        if which == "hr_high": h = rng.randint(CV["hr_high"], 220)
        if which == "hr_low": h = rng.randint(20, CV["hr_low"] - 1)
        hm = health(vit(s, d_, h, rng.random()), degraded=rng.random() < 0.3)
        r = {"quality": rnd_quality(rng), "health": hm, "fall": R(rng.random() * 0.4, post_fall_inactive=False)}
        d = triage(r, rng.randint(0, 12), CFG)
        assert d.status.value == "URGENT" and "critical_vitals" in d.urgent_types, which


def test_data_gap_never_normal_random():
    rng = random.Random(3)
    for _ in range(N):
        kind = rng.choice(["sensor", "unchecked", "q_missing", "h_missing", "degraded", "stale"])
        q, hm = qual(), health()
        dsv = 0
        if kind == "sensor": q = qual(sensor_gap=True, sensor_gap_reason="zero_events")
        if kind == "unchecked": q = qual(quality_checked=False)
        if kind == "q_missing": q = None
        if kind == "h_missing": hm = None
        if kind == "degraded": hm = health(degraded=True)
        if kind == "stale": q = qual(days_since_last_reading=CFG["max_vitals_gap_days"] + rng.randint(1, 20))
        d = triage({"quality": q, "health": hm, "fall": R(0.0, post_fall_inactive=False)}, dsv, CFG)
        assert d.status.value != "NORMAL" and d.data_gap, kind


def hostile_llm():
    """Fake LLM that tries to talk the status down and uses diagnostic wording (repeats forever)."""
    return FakeLLM([{"text": json.dumps({"status": "NORMAL", "alerts": [{"text": "status: normal. The patient has dementia, diagnosis confirmed.", "cites": ["F1"]}],
                                         "changes": [{"text": "status: normal, no action needed.", "cites": ["F1"]}],
                                         "monitor": [{"text": "you have nothing to worry about", "cites": []}]})}])


def _seed_case(eng, day, agent_results):
    with eng.begin() as c:
        for k in ("day", "sensor"):
            eid = c.execute(S.event_log.insert().values(patient_id="p1", day=day, ts="t", kind=k, payload="{}")).inserted_primary_key[0]
            for a in (["quality", "health_monitoring"] if k == "day" else ["fall"]):
                S.insert_ignore(c, S.agent_output, event_id=eid, agent_id=a, patient_id="p1", day=day,
                                result=S.j(agent_results[a]), done_ts=1.0)


def test_hostile_llm_cannot_change_status(tmp_path, monkeypatch):
    import echos.reports.daily.agent as ra
    monkeypatch.setattr(ra, "retrieve", lambda *a, **k: [])   # no embedding model (torch + xgboost in one process can segfault)
    eng = S.get_engine(tmp_path / "t.db")
    _seed_case(eng, "2019-05-05", {"quality": qual(), "health_monitoring": health(), "fall": R(0.97, post_fall_inactive=True)})
    llm = hostile_llm()
    g = build_pipeline(eng, report_llm=llm, report_root=tmp_path, gate=True, weekly_pdf=False)
    process_case(g, "p1", "2019-05-05")
    from echos.reports.daily.agent import drain_all
    drain_all(30)
    with eng.begin() as c:
        st = c.execute(select(S.case_state)).first()
        n_alerts = len(c.execute(select(S.alerts)).fetchall())
    text = next((tmp_path / load_config()["reports"]["daily_dir"]).glob("*.md")).read_text().lower()
    assert llm.calls >= 1                         # URGENT day: the gate lets the LLM run ...
    assert st.status == "URGENT" and n_alerts == 1       # ... and it still cannot change status or alerts
    assert "diagnosis confirmed" not in text and "dementia" not in text and "status: urgent" in text   # the arbiter's status is what is printed


def _gate_run(tmp_path, gate, pats, days):
    from echos.agents import data_quality, health_monitoring
    eng = S.get_engine(tmp_path / f"g{int(gate)}.db")
    with eng.begin() as c:
        for p, d in [(p, d) for p in pats for d in days[p]]:
            eid = c.execute(S.event_log.insert().values(patient_id=p, day=d, ts="t", kind="day", payload="{}")).inserted_primary_key[0]
            for a, m in (("quality", data_quality), ("health_monitoring", health_monitoring)):
                S.insert_ignore(c, S.agent_output, event_id=eid, agent_id=a, patient_id=p, day=d,
                                result=S.j(m.predict(p, d).model_dump(mode="json")), done_ts=1.0)

    llm = FakeLLM([{"error": {"code": 400, "status": "INVALID_ARGUMENT"}}])   # not retried: exactly one request per report that reaches the LLM
    g = build_pipeline(eng, report_llm=llm, report_root=tmp_path / f"r{int(gate)}", gate=gate, weekly_pdf=False)
    for p in pats:
        for d in days[p]:
            process_case(g, p, d)
    from echos.reports.daily.agent import drain_all
    drain_all(60)
    with eng.begin() as c:
        cases = [(r.patient_id, r.day, r.status, r.reasons, r.data_gap) for r in c.execute(select(S.case_state).order_by(S.case_state.c.patient_id, S.case_state.c.day))]
        alerts = sorted((r.alert_id, r.type) for r in c.execute(select(S.alerts)))
    return cases, alerts, llm.calls


def test_gate_on_off_triage_identical(tmp_path, monkeypatch):
    from echos.data.patient_day import load_patient_day
    from echos.sim.replay import pick_patients
    pdy = load_patient_day()
    pats = pick_patients(pdy, 3)
    days = {p: [d.strftime("%Y-%m-%d") for d in sorted(pdy[pdy.patient_id == p].day)[30:50]] for p in pats}
    import echos.reports.daily.agent as ra
    from echos.knowledge.pubmed import PubMedClient
    from echos.reports.retrieval import GatedRetriever
    rag_calls = []
    orig_retrieve = GatedRetriever.retrieve
    monkeypatch.setattr(GatedRetriever, "retrieve", lambda self, status, *a, **k: rag_calls.append(status) or orig_retrieve(self, status, *a, **k))
    monkeypatch.setattr(ra, "_default_pubmed", lambda: PubMedClient(http_get=lambda *a: b"<eSearchResult><IdList></IdList></eSearchResult>", cache_path=tmp_path / "pm.sqlite"))
    monkeypatch.setattr(ra.ReportAgent, "weekly", lambda *a, **k: None)   # the gate is about DAILY reports; weekly uses RAG
    monkeypatch.setattr(ra, "retrieve", lambda *a, **k: [])
    on_cases, on_alerts, on_llm = _gate_run(tmp_path, True, pats, days)
    off_cases, off_alerts, off_llm = _gate_run(tmp_path, False, pats, days)
    assert len(on_cases) == 60
    assert on_cases == off_cases and on_alerts == off_alerts            # triage and alerts identical, gate on vs off
    n_normal = sum(1 for c in on_cases if c[2] == "NORMAL")
    assert on_llm == len(on_cases) - n_normal                           # gate on: LLM only for non-NORMAL days
    assert off_llm == len(off_cases)                                    # gate off: LLM for every day
    assert set(rag_calls) <= {"MONITOR", "URGENT"}                      # a NORMAL day never retrieves (code-side prefetch only for MONITOR / URGENT)
    Path(tmp_path / "gate_result.txt").write_text(f"identical: 60 cases, {n_normal} NORMAL, llm calls on={on_llm} off={off_llm}")


# ------------------------------------------------------------------ synthetic-data guard
def test_guard_rejects_simulated_rows():
    ok = pd.DataFrame({"source": ["tihm"] * 3})
    assert_real_source(ok, "tihm")
    with pytest.raises(AssertionError):
        assert_real_source(pd.concat([ok, pd.DataFrame({"source": ["simulated"]})]), "tihm")
    with pytest.raises(AssertionError):
        assert_real_source(pd.DataFrame({"x": [1]}), "tihm")           # missing column
    with pytest.raises(AssertionError):
        assert_real_source(pd.DataFrame({"source": ["tihm"]}), "sisfall")


def test_training_tables_contain_only_real_rows():
    from echos.agents import fall
    from echos.agents.health import vitals
    from echos.data.patient_day import load_patient_day
    assert_real_source(load_patient_day(), "tihm")
    assert_real_source(vitals.build_features(), "tihm")
    assert_real_source(fall.build_windows(), "sisfall")


def test_trainers_call_the_guard():
    root = Path(__file__).resolve().parents[1]
    for f in ("echos/agents/fall.py", "echos/agents/health/vitals.py", "echos/sim/replay.py"):
        assert "assert_real_source(" in (root / f).read_text(), f
    for f in (root / "echos/agents").rglob("*.py"):   # models never import the simulator or fixtures
        t = f.read_text()
        assert "echos.sim" not in t and "tests.fixtures" not in t, f
