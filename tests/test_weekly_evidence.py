"""Weekly report: layout, vitals detail, fall peak, evidence mapping (per finding), considerations validator. Fakes / mocks only: no network."""
import json

import pandas as pd
import pytest
from pypdf import PdfReader
from sqlalchemy import select

from echos.orchestrator import state as S
from echos.reports import evidence as EV
from echos.reports import facts as F
from echos.reports import weekly_data as WD
from echos.reports import weekly_narrative as WN
from echos.reports import weekly_pdf as WP
from tests.test_weekly import PID, WEEK, data, text_of, world  # noqa: F401  (world is a fixture)


def norm(s):
    return " ".join(s.split())


# ------------------------------------------------------------------ layout
def test_tile_counts_sum_to_days_with_data_and_gap_line(world, tmp_path):
    d = data(world)
    c = d["status_counts"]
    assert c["NORMAL"] + c["MONITOR"] + c["URGENT"] + c["NOT_PROCESSED"] == d["completeness"]["days_with_data"]
    r = WP.build_pdf(d, WN.make_narrative(d, [])["text"], [], tmp_path / "t.pdf")
    t = norm(text_of(r["path"]))
    assert f"({d['completeness']['days_with_data']} days with data)" in t and "Data-gap days (counted within the above): 1" in t
    assert r["pages"] <= 3


def test_chart_captions_do_not_overlap_plot_areas_and_legend_note_appears_once(world, tmp_path):
    d = data(world)
    d["vitals_summary"]["dia"]["baseline_established"] = False          # force a caption
    d["routine_baseline_established"]["night_events"] = False
    WP._CHECK = []
    try:
        r = WP.build_pdf(d, "x", [], tmp_path / "c.pdf")
        boxes = list(WP._CHECK)
    finally:
        WP._CHECK = None
    assert boxes, "no caption was drawn"
    for ax_box, cap in boxes:
        assert not ax_box.overlaps(cap), "a caption intersects its plot area"
    t = norm(text_of(r["path"]))
    assert t.count("Chart legend:") == 1 and "Day (May 2019)" not in t           # one legend note, no per-chart footnote


# ------------------------------------------------------------------ vitals detail and falls in the alerts table
def test_vitals_row_has_the_numbers_and_baseline_wording(world, tmp_path):
    d = data(world)
    a = d["vitals_alerts"][0]
    assert a["values_stored"] and a["values"]["sys"] == 162 and "Systolic 162 / diastolic 82 mmHg" in a["detail"]
    r = WP.build_pdf(d, "x", [], tmp_path / "v.pdf")
    t = norm(text_of(r["path"]))
    assert "Systolic 162 / diastolic 82 mmHg" in t and "heart rate 71 bpm" in t
    assert "usual: systolic" in a["detail"]       # baseline established in this fixture


def test_detail_text_variants_and_old_rows():
    s = F.vitals_detail_text({"sys": 174, "dia": 99}, "2019-04-04 09:12", None, False)
    assert s == "Systolic 174 / diastolic 99 mmHg at 2019-04-04 09:12; baseline not yet established"
    s = F.vitals_detail_text({"sys": 174, "dia": 99, "hr": 80}, None, {"sys": 130, "dia": 80, "hr": 70}, True)
    assert "usual: systolic 130 / diastolic 80 mmHg, heart rate 70 bpm" in s and "probab" not in s.lower()
    assert F.vitals_detail_text({}, None, None, False) == "Vitals differ from usual; values not stored for this day"


def test_old_vitals_row_without_values(world, tmp_path):
    eng, _, kw = world
    with eng.begin() as c:
        row = c.execute(select(S.daily_facts.c.facts).where(S.daily_facts.c.day == "2019-05-07")).scalar()
        f = json.loads(row)
        f["flags"][0]["evidence"] = {"kind": "vitals_alert", "risk": 0.7}
        S.upsert(c, S.daily_facts, ["patient_id", "day"], patient_id=PID, day="2019-05-07", facts=S.j(f))
    d = WD.build_weekly_data(eng, PID, WEEK, **kw)
    assert d["vitals_alerts"][0]["values_stored"] is False
    t = norm(text_of(WP.build_pdf(d, "x", [], tmp_path / "o.pdf")["path"]))
    assert "Vitals differ from usual; values not stored for this day" in t


def test_injected_falls_marked_simulated_and_peak_shown(world, tmp_path):
    d = data(world)
    assert all(f["simulated"] for f in d["falls"])
    t = norm(text_of(WP.build_pdf(d, "x", [], tmp_path / "f.pdf")["path"]))
    assert "peak 2.6 g" in t and "(simulated signal)" in t
    d["falls"][0]["peak_g"] = None
    t = norm(text_of(WP.build_pdf(d, "x", [], tmp_path / "f2.pdf")["path"]))
    assert "peak not recorded" in t


# ------------------------------------------------------------------ peak for every fall event (facts, payload)
def test_peak_stored_for_every_fall_event_including_without_inactivity(tmp_path):
    eng = S.get_engine(tmp_path / "p.db")
    pdy = pd.DataFrame([{"patient_id": "p1", "day": pd.Timestamp("2019-05-05"), "has_activity": True, "has_reading": False, "sys": None, "dia": None, "hr": None,
                         "temp": None, "weight": None, "night_events": 1, "night_bathroom": 0, "total_events": 5}])
    with eng.begin() as c:
        for eid, (score, inactive, peak) in enumerate([(0.95, True, 4.7), (0.91, False, 3.2), (0.88, False, None)], 1):
            res = {"score": score, "evidence": {"post_fall_inactive": inactive, "ts": f"2019-05-05T1{eid}:00:00", **({"peak_svm_g": peak} if peak is not None else {})}}
            c.execute(S.agent_output.insert().values(event_id=eid, agent_id="fall", patient_id="p1", day="2019-05-05", result=S.j(res), done_ts=1.0))
    f = F.build_day_facts(eng, "p1", "2019-05-05", {"status": "URGENT", "reasons": []}, {}, pdy=pdy)
    assert [x["peak_g"] for x in f["falls"]] == [4.7, 3.2, None] and len(f["falls"]) == 3
    assert all("peak_g" in x for x in f["falls"])
    from echos.alerts import payload as P
    p = P.AlertPayload(alert_id="a", alert_type="FALL", patient_id="p1", event_time="2019-05-05T10:00:00", fall_probability=0.9, peak_impact_g=None)
    assert "not recorded" in P.render_text(p)


# ------------------------------------------------------------------ evidence mapping
def src(i, title, text, origin="local", score=0.7):
    return {"id": i, "title": title, "text": text, "source": "S", "origin": origin, "score": score if origin == "local" else None, "url": None}


FALL_KB = src("KB-1", "Falls in older people", "Assess falls and the long lie after a fall.")
BP_KB = src("KB-2", "Hypertension in adults", "Home blood pressure monitoring.")
DEM_KB = src("KB-3", "Dementia", "Agitation and distress in dementia; keep a regular routine.")
PM_FALL = src("PM-9", "Fall detection in older adults: a review", "wearable sensors", "pubmed")
PM_BP = src("PM-8", "Home blood pressure in older adults", "readings", "pubmed")


class Ret:
    """Mocked GatedRetriever: records the calls; returns everything it knows regardless of the finding (the mapping must filter)."""
    def __init__(self, sources):
        self.sources, self.calls = sources, []

    def retrieve(self, status, ft, details=None):
        self.calls.append(ft)
        return {"sources": list(self.sources), "events": ["mock"], "pubmed_failure": None}


def week(**kw):
    d = {"falls": [{"inactivity": True}, {"inactivity": False}], "vitals_alerts": [{"day": "x"}], "unusual_days": [], "any_monitor_or_urgent": True}
    d.update(kw)
    return d


def test_retrieval_is_per_finding_and_sources_are_tied_to_their_finding():
    r = Ret([FALL_KB, BP_KB, DEM_KB, PM_FALL, PM_BP])
    fnd, flat, _ = EV.gather_findings(week(), r, "MONITOR")
    assert r.calls == ["fall_inactivity", "blood_pressure"]                  # one call per finding present (no routine finding: no dementia query)
    m = {f["key"]: f["sources"] for f in fnd}
    assert m["falls"] == ["KB-1", "PM-9"] and m["vitals"] == ["KB-2", "PM-8"]  # each finding got only its own sources; at most 2, PubMed last
    assert {s["id"] for s in flat} == {"KB-1", "PM-9", "KB-2", "PM-8"} and "KB-3" not in {s["id"] for s in flat}
    assert [f["label"] for f in fnd] == ["Possible falls: 2", "Vitals alerts: 1"]


def test_mismatching_and_low_score_sources_are_dropped_and_limits_hold():
    weak = src("KB-5", "Falls in older people", "falls", score=0.3)           # below weekly.evidence.local_min_score
    extra = [src(f"KB-1{i}", "Falls in older people", "falls assessment") for i in range(4)]
    r = Ret([weak, BP_KB, PM_BP, *extra, PM_FALL])
    fnd, _, notes = EV.gather_findings(week(vitals_alerts=[]), r, "MONITOR")
    ids = fnd[0]["sources"]
    assert "KB-5" not in ids and "KB-2" not in ids and "PM-8" not in ids        # weak score, blood-pressure chunk and article do not match falls
    assert len(ids) == 2 and ids[-1] == "PM-9" and sum(i.startswith("PM") for i in ids) == 1
    assert any("dropped" in n for n in notes)
    assert EV.cfg()["local_min_score"] == 0.5                                  # the cut-off is in config.yaml


def test_routine_finding_only_when_such_days_exist():
    r = Ret([DEM_KB])
    fnd, _, _ = EV.gather_findings(week(falls=[], vitals_alerts=[], unusual_days=[{"day": "d"}]), r, "MONITOR")
    assert r.calls == ["routine_change"] and fnd[0]["sources"] == ["KB-3"] and fnd[0]["label"] == "Unusual activity pattern days: 1"
    r2 = Ret([DEM_KB])
    assert EV.gather_findings(week(falls=[], vitals_alerts=[], unusual_days=[]), r2, "MONITOR")[0] == [] and r2.calls == []
    r3 = Ret([FALL_KB])
    fnd, flat, _ = EV.gather_findings(week(any_monitor_or_urgent=False), r3, "MONITOR")
    assert r3.calls == [] and flat == []                                       # no Monitor / Urgent day: no retrieval at all


def test_drop_mismatched_safety_net():
    f = [{"key": "falls", "label": "x", "sources": ["KB-2", "KB-1"], "source_objs": [BP_KB, FALL_KB]}]
    out = EV.drop_mismatched(f)
    assert out[0]["sources"] == ["KB-1"]


def test_considerations_template_and_validator_rejections():
    fnd = [{"key": "falls", "label": "Possible falls: 2", "sources": ["KB-1"], "inactivity": True}, {"key": "vitals", "label": "v", "sources": ["KB-2"]}]
    bl = EV.template_considerations(fnd)
    assert 2 <= len(bl) <= 4 and all(b["cites"] for b in bl)
    two = EV.template_considerations([{**fnd[0], "sources": ["KB-1", "KB-4"]}])
    assert len(two) == 2 and two[1]["cites"] == ["KB-4"]       # a second bullet cites the finding's second source
    ids = {"KB-1", "KB-2", "KB-9"}
    assert EV.validate_considerations(bl, fnd, ids)["ok"]
    assert all(EV.render_bullet(b).endswith("]") for b in bl)
    bad = lambda **k: EV.validate_considerations([{**bl[0], **k}, bl[1]], fnd, ids)   # noqa: E731
    assert any("no citation" in e for e in bad(cites=[])["errors"])
    assert any("not retrieved" in e for e in bad(cites=["KB-77"])["errors"])
    assert any("not a source of the finding" in e for e in bad(cites=["KB-9"])["errors"])      # retrieved, but attached to another finding
    assert any("not a source of the finding" in e for e in bad(cites=["KB-2"])["errors"])
    assert not bad(text="This confirms a diagnosis of hypertension")["ok"]
    assert not bad(text="Stop taking the blood pressure medication")["ok"]
    # LLM mode: a rejected rewrite falls back to the template wording
    out, v = EV.reword(fnd, bl, lambda b: [{**b[0], "cites": []}, b[1]], ids)
    assert out == bl and v["mode"] == "template" and not v["ok"]
    out, v = EV.reword(fnd, bl, lambda b: [{**x, "text": x["text"] + " today"} for x in b], ids)
    assert v["mode"] == "llm" and out[0]["text"].endswith("today")


def test_weekly_pdf_lists_each_finding_with_its_sources_and_json_mapping(world, tmp_path):
    from echos.reports import weekly_report as WR
    from echos.reports.retrieval import GatedRetriever
    eng, _, kw = world
    kb_bp = {"id": "KB-2", "title": "Hypertension in adults", "text": "Home blood pressure monitoring.", "source": "NICE NG136", "page": "5", "finding_type": "blood_pressure", "score": 0.7}
    kb_fall = {"id": "KB-1", "title": "Falls in older people", "text": "Falls assessment.", "source": "NICE NG249", "page": "9", "finding_type": "fall", "score": 0.7}
    ret = GatedRetriever(lambda ft, d=None, k=None: [kb_bp, kb_fall], pubmed=None)
    r = WR.make_weekly(eng, PID, WEEK, None, retriever=ret, root=tmp_path, send=False, **kw)
    t = norm(text_of(r["pdf"]["path"]))
    i_f, i_v, i_c = t.index("Possible falls: 1"), t.index("Vitals alerts: 1"), t.index("Considerations for clinician review")
    assert t.index("[KB-1]", i_f) < i_v < t.index("[KB-2]", i_v) < i_c                 # falls source under falls, vitals source under vitals
    m = {f["key"]: f["sources"] for f in r["provenance"]["findings"]}
    assert m == {"falls": ["KB-1"], "vitals": ["KB-2"], "routine": []} and "No matching source retrieved for this finding" in t
    assert all(b["cites"] for b in r["provenance"]["considerations"]) and r["provenance"]["considerations_validation"]["ok"]
