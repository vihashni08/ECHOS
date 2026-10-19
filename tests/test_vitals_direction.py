"""Step 0: direction-aware vitals findings, why-it-fired text and concrete considerations. Mocked retrieval only: no network."""
from echos.knowledge import pubmed as PM
from echos.knowledge import relevance
from echos.knowledge import retrieve as RT
from echos.reports import evidence as EV
from echos.reports import vitals_why as VW
from echos.reports import weekly_pdf as WP
from echos.reports.retrieval import GatedRetriever
from tests.test_weekly import PID, WEEK, data, text_of, world  # noqa: F401  (world is a fixture)
from tests.test_weekly_evidence import WEEK_D, norm, src

NG136 = {**src("KB-10", "Hypertension in adults", "Measure blood pressure; home blood pressure monitoring for hypertension."), "doc_id": "NICE-NG136"}
NEWS2 = {**src("KB-11", "National Early Warning Score (NEWS) 2", "Systolic blood pressure below 90 scores 3; early warning score for deterioration."), "doc_id": "RCP-NEWS2"}
PM_HYPO = src("PM-1", "Hypotension in older adults: a review", "low blood pressure and falls", "pubmed")
PM_HTN = src("PM-2", "Hypertension treatment in older adults", "blood pressure targets", "pubmed")


class Ret:
    def __init__(self, sources):
        self.sources, self.calls = sources, []

    def retrieve(self, status, ft, details=None):
        self.calls.append(ft)
        return {"sources": list(self.sources), "events": [], "pubmed_failure": None}


def week_of(*types):
    return {"falls": [], "unusual_days": [], "any_monitor_or_urgent": True, "vitals_alerts": [{"day": "2019-05-07", "types": list(types)}]}


# ---------------------------------------------------------------- (a) direction-aware retrieval
def test_low_bp_never_cites_hypertension_guidance():
    fnd, flat, _ = EV.gather_findings(week_of("bp_low"), Ret([NG136, NEWS2, PM_HTN, PM_HYPO]), "MONITOR")
    ids = fnd[0]["sources"]
    assert fnd[0]["key"] == "bp_low" and ids == ["KB-11", "PM-1"]
    assert not {"KB-10", "PM-2"} & {s["id"] for s in flat}


def test_high_bp_does_cite_hypertension_guidance():
    fnd, _, _ = EV.gather_findings(week_of("bp_high"), Ret([NG136, NEWS2, PM_HTN]), "MONITOR")
    assert fnd[0]["key"] == "bp_high" and "KB-10" in fnd[0]["sources"] and "PM-2" in fnd[0]["sources"]


def test_low_bp_with_only_hypertension_retrieved_shows_no_source(world, tmp_path):
    fnd, flat, _ = EV.gather_findings(week_of("bp_low"), Ret([NG136, PM_HTN]), "MONITOR")
    assert fnd[0]["sources"] == [] and flat == []
    d = {**data(world)}
    d["vitals_alerts"] = [{**d["vitals_alerts"][0], "types": ["bp_low"]}]
    t = norm(text_of(WP.build_pdf(d, "x", [], tmp_path / "n.pdf", findings=[{**f, "sources": []} for f in EV.present_findings(d)])["path"]))
    assert "Blood pressure below usual: 1" in t and "No supporting source retrieved for this finding" in t


def test_a_source_that_does_not_match_its_finding_is_dropped():
    fall_kb = src("KB-1", "Falls in older people", "Assess falls and the long lie after a fall.")
    for key in ("bp_high", "bp_low", "hr_high", "hr_low", "temperature"):
        assert not EV.source_ok(key, fall_kb)
    f = [{"key": "bp_low", "label": "x", "sources": ["KB-10", "KB-11", "KB-1"], "source_objs": [NG136, NEWS2, fall_kb]}]
    assert EV.drop_mismatched(f)[0]["sources"] == ["KB-11"]


def test_gated_retriever_filters_by_finding_and_queries_are_direction_specific():
    r = GatedRetriever(lambda ft, d=None, k=None: [NG136, NEWS2], pubmed=None)
    assert [s["id"] for s in r.retrieve("MONITOR", "bp_low")["sources"]] == ["KB-11"]
    assert [s["id"] for s in r.retrieve("MONITOR", "bp_high")["sources"]] == ["KB-10", "KB-11"]
    assert "hypotension" in RT.build_query("bp_low", {"kind": "critical"}) and "hypertension" not in RT.build_query("bp_low", {"kind": "critical"}).lower()
    assert "hypertension" in RT.build_query("bp_high").lower()
    q_low, q_high = PM.build_query("bp_low", {"kind": "critical"}), PM.build_query("bp_high")
    assert "hypotension" in q_low and "hypertension" not in q_low.lower() and "hypertension" in q_high
    assert all(t in RT.QUERIES and t in PM.TERMS for t in relevance.RULES)


# ---------------------------------------------------------------- (b) why it fired
def test_why_names_the_value_that_moved_most_and_its_direction():
    c = VW.classify({"sys": 120, "dia": 58, "hr": 72}, {"sys": 121, "dia": 67, "hr": 71}, zs={"sys": -0.2, "dia": -3.1, "hr": 0.3})
    assert c["why"] == "Diastolic 9 mmHg below usual" and c["types"] == ["bp_low"]
    c = VW.classify({"sys": 170, "dia": 80, "hr": 90}, {"sys": 130, "dia": 80, "hr": 70}, bands={"sys": (130, 5, 120, 140), "dia": (80, 4, 72, 88), "hr": (70, 5, 60, 80)})
    assert c["why"] == "Systolic 40 mmHg above usual" and c["types"] == ["bp_high"]     # largest personal z first, not the largest raw change
    assert VW.classify({"hr": 100}, {"hr": 70}, zs={"hr": 3.0})["types"] == ["hr_high"]
    assert VW.classify({"hr": 45}, {"hr": 70}, zs={"hr": -3.0})["types"] == ["hr_low"]


def test_why_when_no_single_reading_is_outside_the_usual_range():
    c = VW.classify({"sys": 133, "dia": 83}, {"sys": 130, "dia": 80}, zs={"sys": 0.8, "dia": 0.9})
    assert c["why"] == "Diastolic 3 mmHg above usual, but no single reading is outside the usual range; the combination of readings differs from usual"
    assert c["types"] == ["vitals_news2"] and c["top"] is None and c["lead"]["var"] == "dia"
    assert "usual is not yet established" in VW.classify({"sys": 133}, {}, zs={})["why"]


def test_why_never_shows_a_probability_and_critical_names_the_limit():
    c = VW.classify({"sys": 78, "dia": 50}, {"sys": 125, "dia": 70}, zs={"sys": -4.0, "dia": -2.5}, critical=True)
    assert c["types"][0] == "bp_low" and "fixed safety limit of 85" in c["why"] and "47 mmHg below usual" in c["why"]
    assert "probab" not in c["why"].lower() and "risk" not in c["why"].lower()


def test_weekly_row_and_pdf_carry_why_it_fired(world, tmp_path):
    d = data(world)
    a = d["vitals_alerts"][0]
    assert a["types"][0] == "bp_high" and a["why"].startswith("Systolic ") and a["why"].endswith("mmHg above usual")
    assert "probab" not in a["why"].lower() and "0.7" not in a["why"]
    t = norm(text_of(WP.build_pdf(d, "x", [], tmp_path / "w.pdf")["path"]))
    assert "Why it fired: Systolic" in t
    d["vitals_alerts"][0]["why"] = VW.classify({"sys": 131}, {"sys": 130}, zs={"sys": 0.2})["why"]
    t = norm(text_of(WP.build_pdf(d, "x", [], tmp_path / "w2.pdf")["path"]))
    assert "no single reading is outside the usual range; the combination of readings differs from usual" in t


def test_daily_flag_has_direction_and_why(tmp_path):
    import pandas as pd

    from echos.orchestrator import state as S
    from echos.reports import facts as F
    eng = S.get_engine(tmp_path / "d.db")
    pdy = pd.DataFrame([{"patient_id": "p", "day": pd.Timestamp("2019-05-05"), "has_activity": True, "has_reading": True, "sys": 118, "dia": 58, "hr": 70, "temp": None,
                         "weight": None, "night_events": 1, "night_bathroom": 0, "total_events": 5}])
    cv = {"sys": {"current": 118, "baseline": 119, "z": -0.3}, "dia": {"current": 58, "baseline": 67, "z": -3.0}, "hr": {"current": 70, "baseline": 70, "z": 0.0}}
    health = {"vitals": {"probability": 0.9, "current_vs_baseline": cv}, "behaviour": {}}
    f = F.build_day_facts(eng, "p", "2019-05-05", {"status": "MONITOR", "reasons": []}, {"health": health}, pdy=pdy)
    fl = [x for x in f["flags"] if x["evidence"].get("kind") == "vitals_alert"][0]
    assert fl["type"] == "bp_low" and fl["evidence"]["why"] == "Diastolic 9 mmHg below usual"


# ---------------------------------------------------------------- (c) considerations
def test_combination_bullet_still_names_the_value_that_moved_most():
    a = {"day": "2019-05-15", "kind": "alert", "types": ["vitals_news2"], "values": {"sys": 110, "dia": 58}, "top": None,
         "lead": {"var": "dia", "value": 58.0, "base": 67.3, "delta": -9.3}}
    b = EV.template_considerations([{"key": "vitals_news2", "label": "x", "sources": ["KB-11"]}], {"falls": [], "unusual_days": [], "vitals_alerts": [a]})
    assert b[0]["text"].startswith("Diastolic 58 mmHg on 15 May, 9.3 below usual, inside the usual range but part of a combination")


def test_each_bullet_names_a_concrete_value_and_ends_with_a_citation():
    fnd = [{"key": "bp_low", "label": "x", "sources": ["KB-11"]}]
    b = EV.template_considerations(fnd, WEEK_D)
    assert len(b) == 1 and EV.render_bullet(b[0]).startswith("Diastolic 58 mmHg on 15 May, 9 below usual") and EV.render_bullet(b[0]).endswith("[KB-11]")
    assert EV.validate_considerations(b, fnd, {"KB-11"})["ok"]


def test_generic_bullets_are_rejected_and_fewer_bullets_are_printed_not_filler():
    fnd = [{"key": "bp_low", "label": "x", "sources": ["KB-11"]}]
    generic = [{"finding": "bp_low", "text": "Consider whether the pattern of readings over the coming days should be looked at again", "cites": ["KB-11"]}]
    v = EV.validate_considerations(generic, fnd, {"KB-11"})
    assert not v["ok"] and any("generic" in e for e in v["errors"])
    assert not EV.is_concrete("the pattern of readings over the coming days should be looked at again")
    assert EV.is_concrete("Two readings were below usual") and EV.is_concrete("Reading on Tuesday") and not EV.is_concrete("Seen in [KB-65]")
    out, v = EV.reword(fnd, EV.template_considerations(fnd, WEEK_D), lambda b: generic, {"KB-11"})
    assert out[0]["text"].startswith("Diastolic 58") and v["mode"] == "template"       # a generic rewrite is discarded
    assert EV.template_considerations([{"key": "bp_low", "label": "x", "sources": []}], WEEK_D) == []      # no source -> none, not filler
    assert len(EV.template_considerations(fnd, {**WEEK_D, "falls": []})) == 1
