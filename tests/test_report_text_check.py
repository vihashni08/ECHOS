"""The automated report-text proxy check must be able to FAIL: it is tested on good text and on deliberately bad text."""
import json

from scripts.evaluation import report_text_check as T

GOOD = """# Daily report for p1 - 2019-04-02

**Status: MONITOR** - Changed from their usual

## What changed compared with their usual pattern
- Their blood pressure was higher than usual today. [F1]

## What to monitor today
- Check on them in person and ask how they feel.
- Make sure meals and drinks are taken as usual.

---
_Decision-support only, not a medical assessment. Wording: template mode._
"""


def one(md):
    return T.check_one(md)


def test_good_template_text_passes_everything():
    r = one(GOOD)
    assert r["status"] == "MONITOR" and r["banned"] == 0 and not r["leftover"] and r["monitor_items"] == 2 and r["monitor_all_action"] and not r["empty_section"]
    assert r["grade"] < T.MAX_GRADE and r["words"] <= T.MAX_WORDS


def test_syllable_and_readability_basics():
    assert [T.syllables(w) for w in ("cat", "table", "make", "hypertension")] == [1, 2, 1, 4]
    easy = T.readability("The cat sat on the mat. It was warm.")
    hard = T.readability("Pharmacological intervention notwithstanding, haemodynamic instability necessitates multidisciplinary reassessment immediately.")
    assert easy["grade"] < 3 < 15 < hard["grade"] and easy["ease"] > hard["ease"]


def test_banned_and_jargon_wording_is_caught():
    assert one(GOOD.replace("higher than usual", "a sign of hypertension diagnosis"))["banned"] >= 1
    assert one(GOOD.replace("higher than usual", "a SHAP z-score above the threshold"))["banned"] >= 1
    assert one(GOOD.replace("higher than usual", "an agitation probability of 0.8"))["banned"] >= 1
    assert one(GOOD.replace("Make sure meals", "Stop taking the medication; make sure meals"))["banned"] >= 1


def test_missing_next_steps_empty_sections_and_leftover_syntax_are_caught():
    no_action = GOOD.replace("- Check on them in person and ask how they feel.", "- Blood pressure.").replace("- Make sure meals and drinks are taken as usual.", "- Weather.")
    r = one(no_action)
    assert not r["monitor_all_action"]
    assert one(GOOD.replace("- Make sure meals and drinks are taken as usual.\n", ""))["monitor_items"] == 1
    assert one(GOOD.replace("- Their blood pressure was higher than usual today. [F1]\n", ""))["empty_section"]
    assert one(GOOD.replace("higher", "{{ c.text }}"))["leftover"] and one(GOOD.replace("higher", "None"))["leftover"]


def test_long_and_hard_text_fails_the_limits_in_the_aggregate():
    long_hard = GOOD.replace("Their blood pressure was higher than usual today.", " ".join(["Pharmacological haemodynamic reassessment necessitates multidisciplinary consideration."] * 60))
    rows = [one(GOOD), one(long_hard)]
    agg = T.aggregate(rows)
    assert agg["n_reports"] == 2 and agg["pass"]["length_within_limit"] == 1 and agg["pass"]["grade_within_limit"] == 1 and agg["all_checks_pass"] == 1


def test_urgent_needs_an_alerts_section():
    urgent = GOOD.replace("MONITOR", "URGENT")
    assert not one(urgent)["alerts_section"]
    agg = T.aggregate([one(urgent), one(urgent.replace("## What changed", "## Alerts\n- Possible fall at 10:00.\n\n## What changed"))])
    assert agg["pass"]["urgent_total"] == 2 and agg["pass"]["urgent_has_alerts_section"] == 1 and agg["all_checks_pass"] == 1


def test_output_is_labelled_a_proxy_and_has_no_patient_text(tmp_path, monkeypatch):
    out = tmp_path / "o.json"
    monkeypatch.setattr(T, "load_reports", lambda db: [GOOD, GOOD.replace("MONITOR", "NORMAL")])
    assert T.main(["--db", "x.db", "--out", str(out)]) == 0
    d = json.loads(out.read_text())
    assert d["label"].startswith("AUTOMATED PROXY CHECK") and "not a reader study" in d["label"].lower() and d["n_reports"] == 2
    s = out.read_text()
    assert "p1" not in s and "blood pressure" not in s.lower()
    monkeypatch.setattr(T, "load_reports", lambda db: [])
    assert T.main(["--db", "x.db", "--out", str(out)]) == 1


def test_real_stored_reports_if_present_have_no_banned_wording():
    from pathlib import Path
    p = Path(T.ROOT / "reports/metrics/report_text_check.json")
    if p.exists():
        d = json.loads(p.read_text())
        assert d["pass"]["no_banned_wording"] == d["n_reports"] and d["label"].startswith("AUTOMATED PROXY")
