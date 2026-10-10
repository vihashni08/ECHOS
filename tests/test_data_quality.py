import numpy as np
import pandas as pd

from echos.agents import data_quality as dq


def phys(rows):
    return pd.DataFrame(rows, columns=["patient_id", "date", "device_type", "value", "unit"])


def test_out_of_range_becomes_nan_and_is_listed():
    p = phys([("p1", "2019-05-01 08:00", "Heart rate", 300, "bpm"), ("p1", "2019-05-01 08:00", "Heart rate", 70, "bpm"),
              ("p1", "2019-05-01 09:00", "Body Temperature", 20.0, "C"), ("p1", "2019-05-01 09:00", "O/E - muscle mass", 500, "kg")])
    clean, inv, st = dq.clean_physiology(p)
    assert len(clean) == 4  # nothing dropped silently
    assert clean[(clean.device_type == "Heart rate")].value.isna().sum() == 1 and clean.value.notna().sum() == 2
    assert sorted(inv.device_type) == ["Body Temperature", "Heart rate"] and st["invalid_values"] == 2  # no range -> kept


def test_duplicates_dropped_and_sorted_and_non_numeric_recorded():
    p = phys([("p1", "2019-05-02", "Heart rate", 80, "bpm"), ("p1", "2019-05-01", "Heart rate", 70, "bpm"),
              ("p1", "2019-05-01", "Heart rate", 70, "bpm"), ("p1", "2019-05-03", "Heart rate", "n/a", "bpm")])
    clean, inv, st = dq.clean_physiology(p)
    assert st["exact_duplicates_dropped"] == 1 and clean.date.is_monotonic_increasing
    assert (inv.reason == "non_numeric").sum() == 1 and clean.value.isna().sum() == 1


def test_schema_error():
    import pytest
    with pytest.raises(dq.DataQualityError):
        dq.clean_physiology(pd.DataFrame({"patient_id": ["p"]}))


def act(counts, start="2019-05-01", pid="p1"):
    rows = []
    for i, n in enumerate(counts):
        d = pd.Timestamp(start) + pd.Timedelta(days=i)
        rows += [(pid, "Kitchen", d + pd.Timedelta(minutes=k)) for k in range(n)]
    a = pd.DataFrame(rows, columns=["patient_id", "location_name", "date"])
    a["day"] = a.date.dt.normalize()
    return a


def test_sensor_gap_is_not_zero_activity():
    t = dq.sensor_gap_table(act([100, 110, 90, 105, 0, 3, 100]), frac=0.05, min_hist=3).set_index("day")
    assert t.loc["2019-05-05", "sensor_gap"] and t.loc["2019-05-05", "sensor_gap_reason"] == "zero_events"  # in-window silent day
    assert t.loc["2019-05-06", "sensor_gap"] and t.loc["2019-05-06", "sensor_gap_reason"] == "low_events"   # 3 < 5% of 102
    assert not t.loc["2019-05-07", "sensor_gap"]  # gap days did not lower the bar


def test_patient_day_gap_days_have_nan_behaviour_features():
    from echos.data.patient_day import load_patient_day
    d = load_patient_day()
    g = d[d.sensor_gap]
    assert len(g) and not g.has_activity.any()
    assert g.total_events.isna().all() and g.night_events.isna().all()      # NaN, not 0
    ok = d[d.has_activity]
    assert ok.total_events.notna().all() and not ok.sensor_gap.any()


def test_sensor_gap_rule_uses_only_past_data():
    base = [100, 100, 100, 100, 4, 100]
    a = dq.sensor_gap_table(act(base), 0.05, 3)
    b = dq.sensor_gap_table(act(base[:5] + [9999]), 0.05, 3)  # change the FUTURE only
    assert np.allclose(a.past_median.values[:5], b.past_median.values[:5], equal_nan=True)
    assert a.sensor_gap.values[:5].tolist() == b.sensor_gap.values[:5].tolist()


def test_days_since_last_reading_never_uses_future():
    df = pd.DataFrame({"patient_id": "p1", "day": pd.date_range("2019-05-01", periods=6),
                       "has_reading": [False, True, False, False, True, False]})
    assert dq.days_since_reading(df).tolist() == [1, 0, 1, 2, 0, 1]


def test_pass_through_when_agent0_fails():
    r = dq.predict("no-such-patient", "2019-05-01")
    assert r.evidence["quality_checked"] is False and r.flag and "quality unchecked" in r.evidence["reason"]
