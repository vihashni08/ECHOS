"""Live-path fault scenarios (scripts/live_fault_check.py): fakes only, offline. Needs built data and models (skipped in a fresh clone)."""
import pytest

from scripts import live_fault_check as F


def test_duplicate_and_late_events_change_nothing():
    r = F.s_duplicates_and_late_events()
    assert r["duplicates_detected"] == r["resent"] == 200 and r["decisions_changed"] is False and r["extra_rows"] == 0 and r["late_event_stored"] == 1


def test_vitals_detector_down_is_a_data_gap_never_normal():
    r = F.s_vitals_detector_down()
    assert r["days"] >= 1 and r["normal_days"] == 0 and r["all_marked_data_gap"] and r["reasons_mention_degraded"]


def test_a_fall_window_that_could_not_be_analysed_is_not_silently_dropped():
    r = F.s_fall_window_not_analysed()
    if "skipped" in r:
        pytest.skip(r["skipped"])
    assert r["status"] == "MONITOR" and r["data_gap"] and r["processor_alive"]


def test_smtp_down_everywhere_nothing_looks_delivered_and_the_alert_escalates_visibly():
    r = F.s_smtp_down_everywhere()
    if "skipped" in r:
        pytest.skip(r["skipped"])
    assert r["none_looks_delivered"] and r["alert_state"] == ["ESCALATED"] and r["escalation_row_visible"] and r["statuses"] == ["FAILED"]


def test_a_failing_report_agent_never_blocks_the_alert():
    r = F.s_report_agent_down()
    if "skipped" in r:
        pytest.skip(r["skipped"])
    assert r["alert_sent_despite_report_failure"]


def test_burst_of_four_patients_keeps_alert_latency_low():
    r = F.s_burst_and_latency()
    if "skipped" in r:
        pytest.skip(r["skipped"])
    assert r["patients"] == 4 and all(x is not None and x < 2.0 for x in r["inject_to_alert_queued_s"]) and r["events_per_s"] > 100
