"""Weekly clinician PDF: data builder, PDF, narrative, email, orchestrator. FakeMailer / FakeLLM / mocked PubMed only: no network, no SMTP."""
import io
import json
import time

import pandas as pd
import pytest
from pypdf import PdfReader
from sqlalchemy import select

from echos.alerts.dispatcher import AlertDispatcher
from echos.alerts.mailer import DryRunMailer, FakeMailer
from echos.orchestrator import state as S
from echos.reports.weekly import weekly_data as WD
from echos.reports.weekly import weekly_narrative as WN
from echos.reports.weekly import weekly_pdf as WP
from echos.reports.weekly import weekly_report as WR
from echos.reports.llm.fake_llm import FakeLLM
from echos.reports.llm.llm_budget import Backend, Cache, LLMBudget
from echos.reports.retrieval import GatedRetriever

PID, WEEK = "pw", "2019-W19"      # Mon 2019-05-06 .. Sun 2019-05-12
TO = {"caregiver": "care@example.test", "clinician": "doc@example.test"}
KB = {"id": "KB-7", "text": "Check blood pressure again later.", "title": "Hypertension in adults", "source": "NICE NG136 (2019), page 5", "finding_type": "blood_pressure"}


def facts(day, flags=(), falls=(), reading=True, activity=True, gap=False, invalid=()):
    return {"patient_id": PID, "day": day, "status": "NORMAL", "has_reading": reading, "has_activity": activity, "flags": list(flags), "falls": list(falls),
            "quality": {"checked": True, "sensor_gap": gap, "invalid_values": list(invalid), "days_since_last_reading": 0}}


@pytest.fixture
def world(tmp_path):
    eng = S.get_engine(tmp_path / "w.db")
    days = pd.date_range("2019-04-01", "2019-05-11")
    rows = []
    for i, d in enumerate(days):
        rows.append({"patient_id": PID, "day": d, "has_activity": True, "has_reading": True, "sys": 128 + i % 5, "dia": 80 + i % 3, "hr": 70 + i % 4, "temp": 36.5,
                     "weight": 70.0, "night_events": 10 + i % 4, "night_bathroom": 2 + i % 2, "total_events": 300 + 5 * (i % 6), "room_transitions": 80 + i % 7})
    pdy = pd.DataFrame(rows)
    ph = []
    for d, sy in (("2019-05-06", 129), ("2019-05-07", 162), ("2019-05-08", 130), ("2019-05-09", 128), ("2019-05-10", 131), ("2019-05-11", 129)):
        ph += [(PID, f"{d} 09:00:00", "Systolic blood pressure", sy), (PID, f"{d} 09:00:00", "Diastolic blood pressure", 82), (PID, f"{d} 09:00:00", "Heart rate", 71)]
    phys = pd.DataFrame(ph, columns=["patient_id", "date", "device_type", "value"])
    demo = pd.DataFrame([{"patient_id": PID, "age_band": "(80, 90]", "sex": "Female"}])
    fl_bp = {"id": "F1", "type": "blood_pressure", "evidence": {"kind": "vitals_alert", "risk": 0.7, "sys_now": 162.0, "sys_baseline": 130.0, "dia_now": 82.0, "dia_baseline": 81.0,
                                                                "hr_now": 71.0, "hr_baseline": 71.0}}
    fl_ag = {"id": "F1", "type": "agitation", "evidence": {"risk": 0.9, "night_events": 20}}
    fall = {"time": "14:02", "prob": 0.93, "post_fall_inactive": True}
    spec = [("2019-05-06", "NORMAL", False, facts("2019-05-06")), ("2019-05-07", "MONITOR", False, facts("2019-05-07", [fl_bp])),
            ("2019-05-08", "NORMAL", False, facts("2019-05-08", invalid=[{"device_type": "Heart rate", "value": 300}])),
            ("2019-05-09", "URGENT", False, facts("2019-05-09", falls=[fall])), ("2019-05-10", "MONITOR", True, facts("2019-05-10", [fl_ag], reading=False, gap=True)),
            ("2019-05-11", "NORMAL", False, facts("2019-05-11"))]
    with eng.begin() as c:
        for d, st, gap, f in spec:
            S.upsert(c, S.case_state, ["patient_id", "day"], patient_id=PID, day=d, status=st, data_gap=gap, days_since_vitals=0, reasons="[]", report=None, updated_ts=1.0)
            S.upsert(c, S.daily_facts, ["patient_id", "day"], patient_id=PID, day=d, facts=S.j(f))
        S.insert_ignore(c, S.alerts, alert_id=f"{PID}|2019-05-09|fall|5", patient_id=PID, type="fall", time=1.0, status="SENT", event_id=5, event_time="2019-05-09T14:02:11",
                        payload=S.j({"fall_probability": 0.93, "peak_impact_g": 2.6, "post_fall_inactivity": True}))
    return eng, tmp_path, dict(pdy=pdy, phys=phys, demo=demo)


def data(world):
    eng, _, kw = world
    return WD.build_weekly_data(eng, PID, WEEK, **kw)


def text_of(path):
    return "\n".join(p.extract_text() for p in PdfReader(path).pages)


# ------------------------------------------------------------------ data builder
def test_builder_numbers_match_the_database(world):
    d = data(world)
    assert (d["week_start"], d["week_end"], d["age_band"], d["sex"]) == ("2019-05-06", "2019-05-12", "80-90", "Female")
    c = d["status_counts"]
    assert (c["NORMAL"], c["MONITOR"], c["URGENT"], c["DATA_GAP"]) == (3, 2, 1, 1)
    assert d["falls"] == [{"day": "2019-05-09", "time": "14:02", "probability": 0.93, "probability_text": "0.93", "peak_g": 2.6, "inactivity": True, "simulated": True}]
    assert [a["day"] for a in d["vitals_alerts"]] == ["2019-05-07"] and d["vitals_alerts"][0]["sys_now"] == 162.0
    assert [u["day"] for u in d["unusual_days"]] == ["2019-05-10"] and set(d["unusual_days"][0]["z"]) == set(WD.ROUTINE)
    sysv = d["vitals_summary"]["sys"]
    assert sysv["n_readings"] == 6 and sysv["n_flagged"] == 1 and sysv["flagged_days"] == ["2019-05-07"] and 128 < sysv["baseline"] < 132
    comp = d["completeness"]
    assert (comp["days_with_data"], comp["days_with_activity"], comp["days_with_vitals"], comp["sensor_gap_days"], comp["invalid_values_removed"]) == (6, 6, 5, 1, 1)
    assert comp["longest_vitals_gap_days"] == 1 and d["partial_week_note"] == "Partial week: only 6 of 7 days have data."
    assert d["any_monitor_or_urgent"] and set(d["finding_types"]) == {"blood_pressure", "agitation"}


def test_no_model_scores_in_the_weekly_data(world):
    s = json.dumps(WD.narrative_payload(data(world)))
    assert "risk" not in s and "0.7" not in s and "0.9\"" not in s and "agitation_prob" not in s and "probability" in s    # only the FALL probability


def test_baseline_uses_only_past_days(world):
    eng, _, kw = world
    pdy = kw["pdy"].copy()
    pdy.loc[pdy.day >= "2019-05-07", "sys"] = 500        # the future must not move Monday's baseline
    d1 = WD.build_weekly_data(eng, PID, WEEK, **{**kw, "pdy": pdy})
    d0 = data(world)
    assert d1["series"]["vitals"]["sys"]["band"]["2019-05-06"] == d0["series"]["vitals"]["sys"]["band"]["2019-05-06"]


# ------------------------------------------------------------------ PDF
def test_pdf_pages_size_text_footer_and_no_agitation_probability(world, tmp_path):
    d = data(world)
    n = WN.make_narrative(d, [])
    fnd = [{"key": "vitals", "label": "Vitals alerts: 1", "n": 1, "sources": ["KB-7"]}]
    cons = [{"finding": "vitals", "text": "Consider whether repeating the readings at rest would be useful", "cites": ["KB-7"]}]
    r = WP.build_pdf(d, n["text"], [KB | {"origin": "local"}], tmp_path / "w.pdf", findings=fnd, considerations=cons)
    rd = PdfReader(r["path"])
    assert 1 <= len(rd.pages) <= 3 and r["bytes"] < 1_000_000
    t = " ".join(text_of(r["path"]).split())
    for needle in ("Normal 3 | Monitor 2", "Urgent 1", "(6 days with data)", "Data-gap days (counted within the above): 1", "Decision support, not a diagnosis", "Page 1 of", "Key findings",
                   "Findings and supporting sources", "Recommended follow-up", "[KB-7]", "simulated signal",
                   "Possible fall", "Fall probability 0.93", "pw"):
        assert needle in t, needle
    low = t.lower()
    assert "agitation" not in low and "risk" not in low and "vitals probability" not in low
    order = [t.index(h) for h in ("Summary", "Key findings", "Vitals vs personal baseline", "Daily routine vs personal baseline", "Alerts", "Findings and supporting sources", "Recommended follow-up", "Data quality")]
    assert order == sorted(order) and r["headings"][0] == "Weekly clinician report"


def test_pdf_with_no_data_and_empty_week(world, tmp_path):
    eng, _, kw = world
    d = WD.build_weekly_data(eng, PID, "2019-W30", **kw)
    r = WP.build_pdf(d, WN.make_narrative(d, [])["text"], [], tmp_path / "e.pdf")
    assert r["pages"] <= 3 and "No alerts this week" in text_of(r["path"]) and "Partial week" in text_of(r["path"])


# ------------------------------------------------------------------ narrative
def test_narrative_validator_rejects_wrong_number(world):
    d = data(world)
    t = WN.template_items(d)
    assert WN._check(t, d, [])["ok"]
    bad = {"impression": t["impression"], "key_findings": [{"text": " ".join(i["text"] for i in t["key_findings"]).replace("3 Normal", "77 Normal"), "cites": []}], "guidance": []}
    assert any("77" in e for e in WN._check(bad, d, [])["errors"])
    for txt in ("You have hypertension.", "Stop taking the medication."):
        assert not WN._check({"impression": t["impression"], "key_findings": [{"text": " ".join(i["text"] for i in t["key_findings"]) + " " + txt, "cites": []}], "guidance": []}, d, [])["ok"]
    g = {"key_findings": t["key_findings"], "guidance": [{"text": "Recheck the pressure later.", "cites": ["PM-1"]}]}
    assert not WN._check(g, d, [KB])["ok"]                                   # cites a source that was not retrieved
    assert not WN._check({"key_findings": t["key_findings"], "guidance": [{"text": "Recheck later.", "cites": []}]}, d, [KB])["ok"]   # uncited


NOPM = GatedRetriever(lambda *a: [], pubmed=None)


def budget(llm):
    return LLMBudget([Backend("primary", llm)], Cache(":memory:"))


def test_llm_narrative_valid_wrong_number_and_failure_fall_back_to_template(world):
    d = data(world)
    ok = WN.template_items(d)
    n = WN.make_narrative(d, [], budget(FakeLLM([{"text": json.dumps(ok)}])), use_llm=True)
    assert n["mode"] == "llm" and n["provenance"]["model"] == "primary"
    wrong = {"impression": ok["impression"], "key_findings": [{"text": " ".join(i["text"] for i in ok["key_findings"]).replace("3 Normal", "77 Normal"), "cites": []}], "guidance": []}
    llm = FakeLLM([{"text": json.dumps(wrong)}])
    n = WN.make_narrative(d, [], budget(llm), use_llm=True)
    assert n["mode"] == "template" and llm.calls == 2 and n["provenance"]["fallback_reason"].startswith("primary: validation_failed")   # one retry, then template
    n = WN.make_narrative(d, [], budget(FakeLLM([{"error": {"code": 503}}])), use_llm=True)
    assert n["mode"] == "template" and n["validation"]["ok"]


def test_template_report_works_with_every_external_service_down(world, tmp_path):
    eng, _, kw = world

    def down(*a, **k):
        raise RuntimeError("down")
    r = WR.make_weekly(eng, PID, WEEK, None, budget=budget(FakeLLM([{"error": {"code": 503}}])), retriever=GatedRetriever(down, pubmed=None),
                       use_llm=True, root=tmp_path, send=False, **kw)
    assert r["mode"] == "template" and r["pdf"]["pages"] <= 3


# ------------------------------------------------------------------ email
def disp(eng, m=None, **kw):
    return AlertDispatcher(eng, m or FakeMailer(), to=TO, backoff_s=[0.01, 0.02, 0.04], **kw)


def outbox(eng):
    with eng.begin() as c:
        return c.execute(select(S.outbox)).fetchall()


def test_one_email_per_patient_week_with_attachment_dedup_and_force(world, tmp_path):
    eng, _, kw = world
    m = FakeMailer()
    dp = disp(eng, m)
    r = WR.make_weekly(eng, PID, WEEK, dp, root=tmp_path, **kw)
    assert r["queued_alert_id"] == f"weekly|{PID}|{WEEK}" and dp.flush(10)
    assert len(m.sent) == 1 and m.sent[0]["to"] == TO["clinician"]                      # clinician only
    s = m.sent[0]
    assert s["subject"] == f"[ECHOS Weekly] {PID} - 2019-05-06 to 2019-05-12"
    assert len(s["body"].splitlines()) == 5 and "Falls: 1." in s["body"] and "<p>" in s["html"]
    assert s["attachments"][0]["filename"] == f"{PID}_{WEEK}.pdf" and s["attachments"][0]["data"][:4] == b"%PDF"
    assert len(PdfReader(io.BytesIO(s["attachments"][0]["data"])).pages) <= 3
    row = outbox(eng)[0]
    assert (row.status, row.attempts, row.recipient_role, row.attachment_name) == ("SENT", 1, "clinician", f"{PID}_{WEEK}.pdf") and row.attachment_path.endswith(".pdf")
    assert WR.make_weekly(eng, PID, WEEK, dp, root=tmp_path, **kw)["queued_alert_id"] is None and dp.flush(5) and len(m.sent) == 1     # dedup
    r2 = WR.make_weekly(eng, PID, WEEK, dp, root=tmp_path, force=True, **kw)
    assert r2["queued_alert_id"].endswith("|resend-1") and dp.flush(10) and len(m.sent) == 2                                              # force resends
    assert WR.make_weekly(eng, PID, "2019-W18", dp, root=tmp_path, **kw)["queued_alert_id"] and dp.flush(10) and len(m.sent) == 3        # another week is separate


def test_send_weekly_now_uses_llm_when_budget_allows_and_respects_force(world, tmp_path):
    eng, _, kw = world
    m, ok = FakeMailer(), WN.template_items(data(world))
    dp = disp(eng, m)
    r = WR.send_weekly_now(PID, WEEK, eng=eng, dispatcher=dp, budget=budget(FakeLLM([{"text": json.dumps(ok)}])),
                           retriever=GatedRetriever(lambda *a: [KB], pubmed=None), root=tmp_path, **kw)
    assert r["mode"] == "llm" and r["queued_alert_id"] and dp.flush(10) and len(m.sent) == 1
    assert [s["id"] for s in r["provenance"]["sources_retrieved"]] == ["KB-7"]
    with eng.begin() as c:
        prov = json.loads(c.execute(select(S.reports.c.provenance).where(S.reports.c.kind == "weekly_pdf")).scalar())
    assert prov["model"] == "primary" and prov["pdf"]["pages"] <= 3
    assert WR.send_weekly_now(PID, WEEK, eng=eng, dispatcher=dp, budget=None, root=tmp_path, retriever=NOPM, **kw)["queued_alert_id"] is None
    assert WR.send_weekly_now(PID, WEEK, force=True, eng=eng, dispatcher=dp, budget=None, root=tmp_path, retriever=NOPM, **kw)["queued_alert_id"] and dp.flush(10) and len(m.sent) == 2


def test_send_real_false_never_touches_smtp(world, tmp_path):
    eng, _, kw = world
    dp = AlertDispatcher(eng, DryRunMailer(), to=TO, backoff_s=[0.01])      # conftest turns any smtplib.SMTP use into a failure
    WR.make_weekly(eng, PID, WEEK, dp, root=tmp_path, **kw)
    assert dp.flush(10)
    r = outbox(eng)[0]
    assert (r.channel, r.status) == ("dryrun", "SENT")


def test_failing_mailer_ends_failed_without_blocking(world, tmp_path):
    eng, _, kw = world
    m = FakeMailer(fail_for={"*"})
    dp = disp(eng, m)
    t0 = time.time()
    WR.make_weekly(eng, PID, WEEK, dp, root=tmp_path, **kw)
    assert time.time() - t0 < 5          # queued, not sent inline
    assert dp.flush(10)
    r = outbox(eng)[0]
    assert (r.status, r.attempts) == ("FAILED", 3) and "ConnectionError" in r.error and not m.sent


def test_missing_clinician_address_is_a_visible_failure(world, tmp_path):
    eng, _, kw = world
    dp = AlertDispatcher(eng, FakeMailer(), to={"caregiver": "x@example.test", "clinician": ""})
    WR.make_weekly(eng, PID, WEEK, dp, root=tmp_path, **kw)
    assert outbox(eng)[0].status == "FAILED"


# ------------------------------------------------------------------ orchestrator
def _sunday_graph(tmp_path, mailer, llm=None):
    from tests.test_orchestrator import R, health, qual
    from tests.test_safety import _seed_case
    from echos.orchestrator.pipeline import build_pipeline
    eng = S.get_engine(tmp_path / "g.db")
    _seed_case(eng, "2019-05-05", {"quality": qual(), "health_monitoring": health(), "fall": R(0.0)})    # a Sunday
    g = build_pipeline(eng, report_llm=llm, report_root=tmp_path, gate=True, dispatcher=disp(eng, mailer))
    return eng, g


def test_weekly_node_builds_pdf_and_queues_email_in_background(tmp_path):
    from echos.orchestrator.pipeline import process_case
    from echos.reports.daily.agent import drain_all
    m = FakeMailer()
    eng, g = _sunday_graph(tmp_path, m)
    assert process_case(g, "p1", "2019-05-05") is True
    drain_all(60)
    assert g.dispatcher.flush(10)
    assert [s["to"] for s in m.sent] == [TO["clinician"]] and m.sent[0]["subject"].startswith("[ECHOS Weekly] p1 - 2019-04-29 to 2019-05-05")
    assert list((tmp_path / "data/reports/weekly").glob("p1_2019-W18.pdf"))


def test_a_failed_pdf_build_does_not_break_the_day(tmp_path, monkeypatch):
    from echos.orchestrator.pipeline import process_case
    from echos.reports.daily.agent import drain_all

    def boom(*a, **k):
        raise RuntimeError("pdf exploded")
    monkeypatch.setattr(WP, "build_pdf", boom)
    m = FakeMailer()
    eng, g = _sunday_graph(tmp_path, m)
    assert process_case(g, "p1", "2019-05-05") is True
    drain_all(60)
    g.dispatcher.flush(5)
    with eng.begin() as c:
        st = c.execute(select(S.case_state.c.status)).scalar()
    assert st == "NORMAL" and not m.sent and not outbox(eng)


def test_simulation_uses_the_template_narrative_unless_configured(tmp_path, monkeypatch):
    from echos.orchestrator.pipeline import process_case
    from echos.reports.daily.agent import drain_all
    seen = []
    real = WN.make_narrative
    monkeypatch.setattr(WN, "make_narrative", lambda *a, **k: seen.append(k.get("use_llm")) or real(*a, **k))
    eng, g = _sunday_graph(tmp_path, FakeMailer(), llm=FakeLLM([{"error": {"code": 400}}]))
    process_case(g, "p1", "2019-05-05")
    drain_all(60)
    assert seen == [False]      # weekly.llm_narrative_in_simulation is false by default


# ------------------------------------------------------------------ baselines need history; formatting
def test_fall_probability_formatting():
    assert [WD.fmt_prob(x) for x in (0.93, 0.994, 0.996, 1.0, 0.5, None)] == ["0.93", "0.99", ">0.99", ">0.99", "0.50", "n/a"]
    d = {"falls": [{"day": "2019-05-09", "time": "14:02", "probability": 0.998, "probability_text": ">0.99", "inactivity": True}], "status_counts": {"NORMAL": 0, "MONITOR": 0, "URGENT": 1, "DATA_GAP": 0},
         "completeness": {"days_with_data": 1, "days_with_vitals": 1, "days_with_activity": 1, "sensor_gap_days": 0, "invalid_values_removed": 0}, "week_start": "a", "week_end": "b",
         "days_in_week": 7, "unusual_days": [], "partial_week_note": None,
         "vitals_summary": {k: {"name": k, "unit": "u", "n_readings": 0, "baseline_established": False} for k in ("sys", "dia", "hr", "temp", "weight")}}
    assert "probability >0.99" in " ".join(i["text"] for i in WN.template_items(d)["key_findings"])


def test_flagged_count_grammar(world):
    d = data(world)
    t = " ".join(i["text"] for i in WN.template_items(d)["key_findings"])
    assert "1 of 6 readings was outside the personal band" in t and "0 of 6 readings were outside" in t       # sys has one flagged reading


def short_history(world, n_days):
    eng, _, kw = world
    pdy = kw["pdy"]
    keep = pdy[pdy.day >= pd.Timestamp("2019-05-11") - pd.Timedelta(days=n_days + 5)]
    keep = keep[(keep.day >= "2019-05-06") | (keep.day >= pd.Timestamp("2019-05-06") - pd.Timedelta(days=n_days))]
    return WD.build_weekly_data(eng, PID, WEEK, **{**kw, "pdy": keep})


def test_vitals_baseline_needs_5_earlier_readings(world, tmp_path):
    d = short_history(world, 4)                          # only 4 earlier days before the week
    sysv = d["vitals_summary"]["sys"]
    assert not any(p["lo"] is not None for p in d["series"]["vitals"]["sys"]["points"][:1])      # Monday: 4 earlier readings -> no band
    d5 = short_history(world, 5)
    assert d5["series"]["vitals"]["sys"]["band"]["2019-05-06"] is not None                       # exactly 5 earlier readings -> band
    # no history at all: nothing to compare with
    eng, _, kw = world
    d0 = WD.build_weekly_data(eng, PID, WEEK, **{**kw, "pdy": kw["pdy"][kw["pdy"].day >= "2019-05-09"]})
    v = d0["vitals_summary"]["sys"]
    assert v["baseline_established"] is False and v["baseline"] is None and v["n_flagged"] is None and v["note"] == WD.NOT_ESTABLISHED
    assert all(p["lo"] is None and not p["flagged"] for p in d0["series"]["vitals"]["sys"]["points"])
    text = " ".join(i["text"] for i in WN.template_items(d0)["key_findings"])
    assert "personal baseline not yet established (fewer than 5 earlier readings)" in text and "outside the personal band" not in text and WN._check(WN.template_items(d0), d0, [])["ok"]
    r = WP.build_pdf(d0, WN.make_narrative(d0, [])["text"], [], tmp_path / "nb.pdf")
    t = " ".join(text_of(r["path"]).split())
    assert "not yet established (fewer than 5 earlier readings)" in t and "Only this week's own values are shown" in t and r["pages"] <= 3


def test_routine_baseline_needs_7_earlier_days(world):
    eng, _, kw = world
    pdy = kw["pdy"]
    d6 = WD.build_weekly_data(eng, PID, WEEK, **{**kw, "pdy": pdy[pdy.day >= "2019-04-30"]})     # 6 earlier days before Monday 05-06
    assert d6["series"]["routine"]["night_events"]["band"]["2019-05-06"] is None
    d7 = WD.build_weekly_data(eng, PID, WEEK, **{**kw, "pdy": pdy[pdy.day >= "2019-04-29"]})
    assert d7["series"]["routine"]["night_events"]["band"]["2019-05-06"] is not None


# ------------------------------------------------------------------ real orchestrator weekly path attaches the retriever
def test_orchestrator_weekly_pdf_lists_local_and_pubmed_sources(tmp_path, monkeypatch):
    from echos.knowledge.pubmed import PubMedClient
    from echos.orchestrator.pipeline import process_case
    from echos.reports.daily import agent as ra
    from echos.reports.daily.agent import drain_all
    from tests.test_report_llm import FakeNCBI
    KBF = KB | {"title": "Falls in older people", "text": "Assess the circumstances of a fall in an older adult.", "score": 0.7}
    monkeypatch.setattr(ra, "retrieve", lambda ft, d=None, k=None: [KBF | {"finding_type": ft}])           # mocked local index
    ncbi = FakeNCBI()
    monkeypatch.setattr(ra, "_default_pubmed", lambda: PubMedClient(http_get=ncbi, cache_path=tmp_path / "pm.sqlite"))   # mocked PubMed
    from tests.test_orchestrator import R, health, qual
    from tests.test_safety import _seed_case
    from echos.orchestrator.pipeline import build_pipeline
    eng = S.get_engine(tmp_path / "g.db")
    _seed_case(eng, "2019-05-05", {"quality": qual(), "health_monitoring": health(), "fall": R(0.97, post_fall_inactive=True)})   # URGENT Sunday
    g = build_pipeline(eng, report_llm=None, report_root=tmp_path, gate=True, dispatcher=disp(eng, FakeMailer()))
    assert process_case(g, "p1", "2019-05-05")
    drain_all(60)
    g.dispatcher.flush(10)
    t = text_of(next((tmp_path / "data/reports/weekly").glob("p1_*.pdf")))
    assert "[KB-7]" in t and "Possible falls: 1" in t
    assert "[PM-12345678]" not in t                      # the retrieved PubMed article is about blood pressure: it does not match the falls finding, so it is dropped
    assert any("esearch" in u for u, *_ in ncbi.calls)
    with eng.begin() as c:
        prov = json.loads(c.execute(select(S.reports.c.provenance).where(S.reports.c.kind == "weekly_pdf")).scalar())
    assert {x["id"] for x in prov["sources_retrieved"]} == {"KB-7"} and prov["findings"][0]["sources"] == ["KB-7"]


def test_no_retrieval_for_a_week_without_monitor_or_urgent_days(world, tmp_path):
    eng, _, kw = world
    calls = []
    r = WR.make_weekly(eng, PID, "2019-W30", None, retriever=GatedRetriever(lambda *a: calls.append(1) or [KB], pubmed=None), root=tmp_path, send=False, **kw)
    assert not calls and r["provenance"]["sources_retrieved"] == []


def test_pdf_has_an_impression_bullet_findings_and_a_follow_up_table(world, tmp_path):
    d = data(world)
    n = WN.make_narrative(d, [])
    assert n["items"]["impression"] and len(n["items"]["key_findings"]) >= 4            # one bullet per topic, not one paragraph
    fnd = [{"key": "falls", "label": "Possible falls: 1", "n": 1, "sources": ["KB-7"]}, {"key": "routine", "label": "Unusual activity pattern days: 1", "n": 1, "sources": []}]
    cons = [{"finding": "falls", "text": "1 possible fall this week; consider a post-fall check", "cites": ["KB-7"]}]
    r = WP.build_pdf(d, n["text"], [KB | {"origin": "local"}], tmp_path / "w.pdf", findings=fnd, considerations=cons, narrative_items=n["items"])
    t = " ".join(text_of(r["path"]).split())
    assert "Impression" in t and "Recommended follow-up" in t and "Within 2 days" in t and "[1]" in t
    assert "no supporting source was retrieved for this finding" in t         # a finding without a source still gets a follow-up pointer, never silence
    assert t.index("Impression") < t.index("Key findings") < t.index("Findings and supporting sources") < t.index("Recommended follow-up")
