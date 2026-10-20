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


def test_completeness_counts_only_vitals_and_validity():
    assert dq.completeness(True, 0) == 1.0 and dq.completeness(False, 0) == 0.2 and dq.completeness(True, 2) == 0.8 and dq.completeness(False, 1) == 0.0


def test_room_sensor_activity_is_not_part_of_the_quality_gate():
    import inspect
    assert not hasattr(dq, "sensor_gap_table") and not hasattr(dq, "clean_activity")
    from echos.schemas import DataQualityResult
    assert "sensor_gap" not in DataQualityResult.model_fields and "sensor_gap" not in inspect.getsource(dq.check_day)


def test_patient_day_calendar_is_continuous_between_first_and_last_record():
    from echos.data.patient_day import _calendar
    raw = pd.DataFrame({"patient_id": ["p1", "p1", "p2"], "day": pd.to_datetime(["2019-05-01", "2019-05-05", "2019-05-03"]), "sys": [120.0, 130.0, 110.0]})
    out = _calendar(raw, ["patient_id", "day"])
    p1 = out[out.patient_id == "p1"]
    assert list(p1.day.dt.strftime("%m-%d")) == ["05-01", "05-02", "05-03", "05-04", "05-05"] and p1.sys.isna().sum() == 3     # the silent days are rows, not holes
    assert len(out[out.patient_id == "p2"]) == 1


def test_built_patient_day_table_has_no_room_sensor_columns():
    from echos.data.patient_day import load_patient_day
    d = load_patient_day()
    assert not ({"sensor_gap", "sensor_gap_reason", "has_activity", "total_events"} & set(d.columns))
    g = d.sort_values(["patient_id", "day"]).groupby("patient_id").day.diff().dropna()
    assert (g == pd.Timedelta(days=1)).all()                    # one row per calendar day inside each patient's span


def test_days_since_last_reading_never_uses_future():
    df = pd.DataFrame({"patient_id": "p1", "day": pd.date_range("2019-05-01", periods=6),
                       "has_reading": [False, True, False, False, True, False]})
    assert dq.days_since_reading(df).tolist() == [1, 0, 1, 2, 0, 1]


def test_pass_through_when_agent0_fails():
    r = dq.predict("no-such-patient", "2019-05-01")
    assert r.evidence["quality_checked"] is False and r.flag and "quality unchecked" in r.evidence["reason"]
