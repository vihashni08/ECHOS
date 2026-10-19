"""Validator calibration: natural phrasings that must PASS and wrong / unsafe ones that must FAIL. No model, no network."""
import pytest

from echos.reports.daily.validator import validate_report

PAYLOAD = {"facts": {"day": "2019-05-15", "vitals": {"sys": {"value": 143.4, "baseline": 128.0, "delta": 9.3, "pct": 0.104}, "hr": {"value": 71, "baseline": 66.25}},
                     "routine": {"night_events": {"value": 24, "baseline": 12}}, "flags": [{"id": "F1"}]}}
G = {"section": "guidance", "ids": ["KB-7"], "texts": {"KB-7": "Check blood pressure again later and review medicines with the clinician."}}


def check(text, section="changes", cites=("F1",), **kw):
    out = {"status": "MONITOR", section: [{"text": text, "cites": list(cites)}]}
    return validate_report(out, PAYLOAD, [], ["F1", "KB-7"], [], **kw)


@pytest.mark.parametrize("text", [
    "Systolic pressure was about 9 below usual.",                    # 9.3 rounded to a whole number
    "Systolic pressure was 9.3 above usual.",
    "Readings were about 143 on that day.",                         # 143.4 rounded
    "Systolic pressure was 143.4 mmHg, above usual 128.",
    "On May 15 the reading was higher than usual.",                 # date parts come from 2019-05-15
    "On 15 May the reading was higher than usual.",
    "The reading on 15/05 was higher than usual.",
    "Night-time activity was 24 events against a usual 12, twice the usual.",
    "Heart rate was 71 against its usual 66.",                      # 66.25 rounded
    "The reading was about 10% higher than usual.",                 # 0.104 as a percentage
    "Pressure was roughly 10 percent above usual.",
])
def test_natural_phrasings_pass(text):
    r = check(text)
    assert r["ok"], r["errors"]


@pytest.mark.parametrize("text", [
    "Systolic pressure was 171 against usual 128.",                 # number not in the input
    "Systolic pressure rose by 40 points.",
    "Night-time activity was 35 events.",
    "Heart rate was about 40% above usual.",                         # a percentage that matches no stored fraction
])
def test_numbers_not_in_input_fail(text):
    r = check(text)
    assert not r["ok"] and "number_not_in_input" in r["codes"]


def test_uncited_guidance_fails_and_unretrieved_citation_fails():
    out = {"status": "MONITOR", "guidance": [{"text": "Recheck the blood pressure later in the day.", "cites": []}]}
    r = validate_report(out, PAYLOAD, [], ["F1", "KB-7"], [], G)
    assert not r["ok"] and "guidance_uncited" in r["codes"]
    out["guidance"][0]["cites"] = ["PM-1"]
    assert "guidance_not_retrieved" in validate_report(out, PAYLOAD, [], ["F1", "KB-7"], [], G)["codes"]
    out["guidance"][0]["cites"] = ["KB-7"]
    assert validate_report(out, PAYLOAD, [], ["F1", "KB-7"], [], G)["ok"]


@pytest.mark.parametrize("text", [
    "This looks like a diagnosis of hypertension.", "You have high blood pressure.", "She is suffering from stress.", "He has developed hypertension.",
    "The doctor confirmed that he has dementia.",
])
def test_diagnostic_wording_fails(text):
    r = check(text)
    assert not r["ok"] and "diagnostic_wording" in r["codes"], r


@pytest.mark.parametrize("text", [
    "Stop taking the medication.", "Increase the dose of the tablets.", "Ask about a prescription.", "Take 20 mg of it.", "Skip the pills today.",
    "Adjust the antihypertensive.",
])
def test_medication_advice_fails(text):
    assert not check(text)["ok"], text


def test_score_wording_fails():
    r = check("The agitation probability was high.")
    assert not r["ok"] and "score_quoted" in r["codes"]


def test_failure_report_names_rule_and_location_not_the_text():
    r = check("Systolic pressure was 171 against usual 128.")
    assert r["findings"][0]["rule"] == "number_not_in_input" and r["findings"][0]["detail"] == "number 171 not in input at changes[0]"
