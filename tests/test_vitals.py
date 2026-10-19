import pandas as pd
import pytest

from echos.agents.health import vitals as p
from echos.data.patient_day import load_patient_day
from echos.schemas import AgentResult


@pytest.fixture(scope="module")
def feats():
    return p.build_features()


def test_no_duplicates(feats):
    assert not feats.duplicated(["patient_id", "day"]).any()


def test_rolling_features_no_leakage():
    """Features for day <= cutoff must be identical when all later data is removed."""
    pdy = load_patient_day()
    full = p.build_features(pdy).set_index(["patient_id", "day"])
    cutoff = pd.Timestamp("2019-05-15")
    part = p.build_features(pdy[pdy.day <= cutoff]).set_index(["patient_id", "day"])
    cols = [c for c in p.FEATURES]
    a, b = full.loc[part.index, cols], part[cols]
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9, atol=1e-9)


def test_baseline_excludes_current_day(feats):
    g = feats[feats.patient_id == feats.patient_id.iloc[0]].sort_values("day")
    assert pd.isna(g.sys_base.iloc[0]) and g.n_prior.iloc[0] == 0


def test_predict_schema(feats):
    r = feats.dropna(subset=["sys_z"]).iloc[50]
    out = p.predict(r.patient_id, r.day)
    assert isinstance(out, AgentResult) and out.agent == "physiological"
    assert 0 <= out.score <= 1
    assert len(out.evidence["top_features"]) == 3
    assert set(out.evidence["current_vs_baseline"]) == {"sys", "dia", "hr"}


def test_predict_no_reading():
    out = p.predict("0697d", "1999-01-01")
    assert out.score == 0 and not out.flag and out.evidence["reason"] == "no_vitals_reading"
