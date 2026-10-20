"""Case Investigator computation (echos/team/evidence_tools.py): past-only, no labels from today or later, synthetic data only."""
import pandas as pd

from echos.orchestrator import state as S
from echos.team import evidence_tools as ET
from echos.team.investigator import Investigator


def table(sys_vals, labels=None, pid="p1", start="2019-04-01"):
    days = pd.date_range(start, periods=len(sys_vals))
    d = pd.DataFrame({"patient_id": pid, "day": days, "sys": [float(x) for x in sys_vals], "dia": 70.0, "hr": 70.0, "has_reading": True,
                      "bp_alert": 0, "pulse_alert": 0, "water_alert": 0})
    for i in (labels or []):
        d.loc[i, "bp_alert"] = 1
    return d


def test_robust_z_uses_only_the_past_and_needs_enough_readings():
    assert ET.robust_z([120, 121], 150, "sys") == (None, None)
    z, base = ET.robust_z([120, 121, 119, 120, 122], 150, "sys")
    assert base == 120 and z > 5


def test_trend_confirms_a_rise_but_not_a_flat_series():
    past = [120, 121, 119, 120, 122, 121, 120]
    rising = ET.trend_check(past + [128, 134], 142, "sys")
    assert rising["confirmed"] and rising["moving_away"] and rising["slope_per_reading"] > 0
    flat = ET.trend_check(past + [140, 120], 121, "sys")
    assert not flat["outside_usual"] and not flat["confirmed"]
    assert not ET.trend_check(past + [150, 120], 150, "sys")["confirmed"]       # a jump after a return to usual is not a trend


def test_personal_history_never_reads_today_or_later():
    sysv = [120, 121, 119, 120, 122, 121, 120, 150, 120, 121, 120, 151, 120, 120]
    t = table(sysv, labels=[7, 11, 13])          # the label on day 13 (today below) must be ignored
    h = ET.personal_history(t, "p1", str(t.day.iloc[13].date()))
    assert h["prior_deviation_days"] == 2 and h["prior_deviation_days_with_event"] == 2 and h["prior_event_labels"] == 2
    h2 = ET.personal_history(t, "p1", str(t.day.iloc[8].date()))
    assert h2["prior_deviation_days"] == 1 and h2["prior_event_labels"] == 1


def _inv(tmp_path, tbl):
    inv = Investigator(S.get_engine(tmp_path / "i.db"))
    inv._history_table = lambda: tbl
    return inv


def _results(now):
    return {"health": {"vitals": {"available": True, "flag": True, "persistence": {"persistent": False},
                                  "current_vs_baseline": {"sys": {"current": now, "baseline": 120}, "dia": {"current": 70}, "hr": {"current": 70}}},
                       }, "quality": {"evidence": {"quality_checked": True}}, "fall": None}


def test_investigator_computes_trend_history_and_uses_them(tmp_path):
    t = table([120, 121, 119, 120, 122, 121, 120, 150, 120, 121, 120, 151, 126, 134, 142], labels=[7, 11])
    f = _inv(tmp_path, t).investigate("p1", str(t.day.iloc[14].date()), {"status": "MONITOR", "reasons": ["vitals alert p=0.5"], "urgent_types": []},
                                      _results(142))
    tools = [s["tool"] for s in f["trace"]]
    assert "check_trend" in tools and "check_personal_history" in tools and len(tools) <= 7
    assert f["trend"]["confirmed"] == ["sys"] and f["personal_history"]["prior_deviation_days_with_event"] == 2
    assert f["supports"] and f["evidence_level"] == "MODERATE" and f["context"]       # history moves the level; the trend is context only


def test_event_free_history_is_context_only_and_critical_is_never_lowered(tmp_path):
    t = table([120, 121, 119, 120, 122, 121, 120, 150, 120, 151, 120, 152, 120, 153, 120, 150])
    inv = _inv(tmp_path, t)
    day = str(t.day.iloc[15].date())
    weak = inv.investigate("p1", day, {"status": "MONITOR", "reasons": ["vitals alert p=0.5"], "urgent_types": []}, _results(150))
    assert any("no recorded clinical event" in c for c in weak["context"]) and weak["evidence_level"] == "LIMITED" and not weak["supports"]
    crit = inv.investigate("p1", day, {"status": "URGENT", "reasons": ["critical vitals"], "urgent_types": ["critical_vitals"]}, _results(190))
    assert crit["evidence_level"] == "STRONG"
