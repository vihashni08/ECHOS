"""Specific wording: the case file and reports name the vital, its value, the person's own usual and the difference; the validator rejects a vague LLM sentence."""
import pytest

from echos.orchestrator import state as S
from echos.reports import specifics as SP
from echos.reports.agent import daily_template_items
from echos.reports.validator import validate_report
from echos.team.investigator import Investigator

V = {"sys": 168.0, "dia": 96.0, "hr": 82.0}
B = {"sys": 143.0, "dia": 78.0, "hr": 80.0}
Z = {"sys": 2.4, "dia": 2.1, "hr": 0.3}


@pytest.fixture
def eng(tmp_path):
    return S.get_engine(tmp_path / "t.db")


def _results(prob=0.9, fixed=False, degraded=False, beh_prob=0.0):
    cvb = {k: {"current": V[k], "baseline": B[k], "z": Z[k]} for k in V}
    return {"quality": {"evidence": {"quality_checked": True}}, "fall": {"score": 0.0, "flag": False, "evidence": {}},
            "health": {"degraded": degraded, "vitals": {"available": True, "probability": prob, "flag": prob > 0.5, "current_vs_baseline": cvb, "fixed_rule_triggered": fixed,
                                                       "persistence": {"persistent": False}},
                       "behaviour": {"available": True, "probability": beh_prob, "flag": False, "z": {"night_bathroom": 3.1, "total_events": 0.2}}}}


def test_sentences_name_the_vital_value_usual_and_direction():
    s = SP.vital_sentences(V, B, Z)
    assert s[0].startswith("Systolic blood pressure 168 mmHg is 25 mmHg above this person's usual of 143 mmHg") and "outside their usual range" in s[0]
    assert any(x.startswith("Diastolic blood pressure 96 mmHg is 18 mmHg above") for x in s) and not any(x.startswith("Heart rate") for x in s)   # hr is within usual


def test_actions_are_direction_aware_and_name_the_measure():
    hi, lo = SP.vital_actions(["bp_high"], V)[0], SP.vital_actions(["bp_low"], V)[0]
    assert "168/96 mmHg" in hi and "headache" in hi and "after standing" in lo and hi != lo
    assert "pulse" in SP.vital_actions(["hr_high"], V)[0] and "weigh" in SP.vital_actions(["weight_change"], V)[0].lower()


def test_case_file_headline_names_the_vital_and_does_not_count_a_detector_below_its_threshold(eng):
    f = Investigator(eng).investigate("p1", "2019-05-05", {"status": "MONITOR", "reasons": ["vitals alert p=0.90"], "urgent_types": []}, _results(prob=0.9))
    assert "Systolic blood pressure 168 mmHg is 25 mmHg above" in f["headline"] and f["specifics"] and "168/96" in " ".join(f["next_checks"])
    # vitals evidence below triage.vitals_prob and a fixed rule that fires only as a fallback: NOT flagged, so not a second source and not STRONG
    g = Investigator(eng).investigate("p1", "2019-05-06", {"status": "MONITOR", "reasons": ["agitation p=0.16"], "urgent_types": []},
                                      _results(prob=0.2, fixed=True, beh_prob=0.9))
    assert g["detectors"]["flagged"] == ["behaviour"] and g["agreement"]["independent_sources"] == 1 and g["evidence_level"] != "STRONG"
    assert "night-time bathroom visits is 3.1 standard deviations above" in g["headline"].lower()


def test_degraded_monitoring_uses_the_fixed_rule(eng):
    d = Investigator(eng).read_detectors(_results(prob=0.0, fixed=True, degraded=True), {"urgent_types": []})
    assert d["flagged"] == ["vitals"]


FLAGS = [{"id": "F1", "type": "bp_high", "evidence": {"specifics": SP.vital_sentences(V, B, Z)}}]
PAYLOAD = {"facts": {"flags": FLAGS}}


def _chk(text):
    out = {"status": "MONITOR", "changes": [{"text": text, "cites": ["F1"]}], "monitor": [], "guidance": []}
    return validate_report(out, PAYLOAD, ["F1"], ["F1"], ["changes"], None, expected_status="MONITOR", flags=FLAGS)


def test_validator_rejects_a_change_that_quotes_none_of_the_recorded_values():
    bad = _chk("The blood pressure was higher than usual today.")
    assert not bad["ok"] and "not_specific" in bad["codes"]
    assert _chk("Systolic blood pressure was 168 mmHg, 25 mmHg above the usual 143 mmHg.")["ok"]


def test_template_report_quotes_the_numbers_and_a_specific_action():
    facts = {"flags": [{"id": "F1", "type": "bp_high", "evidence": {"kind": "vitals_alert", "values": V, "baselines": B, "baseline_established": True,
                                                                   "sys_now": 168.0, "sys_baseline": 143.0, "why": "Systolic 25 mmHg above usual",
                                                                   "specifics": FLAGS[0]["evidence"]["specifics"], "action_hints": SP.vital_actions(["bp_high"], V)}}]}
    out = daily_template_items(facts)
    assert "25 mmHg above this person's usual of 143 mmHg" in out["changes"][0]["text"]
    assert any("Measure blood pressure again" in m["text"] for m in out["monitor"])
