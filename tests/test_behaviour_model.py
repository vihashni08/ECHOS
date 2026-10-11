import pandas as pd
import pytest

from echos.agents.health import behaviour as b
from echos.data.patient_day import load_patient_day
from echos.schemas import AgentResult


@pytest.fixture(scope="module")
def feats():
    return b.build_features()


def test_no_leakage():
    pdy = load_patient_day()
    cols = [f"{f}_z" for f in b.FEATS] + [f"{f}_cusum" for f in b.FEATS]
    full = b.build_features(pdy).set_index(["patient_id", "day"])
    part = b.build_features(pdy[pdy.day <= pd.Timestamp("2019-05-15")]).set_index(["patient_id", "day"])
    pd.testing.assert_frame_equal(full.loc[part.index, cols], part[cols], check_exact=False, atol=1e-9)


def test_cusum_nonneg_and_score_range(feats):
    assert (feats[[f"{f}_cusum" for f in b.FEATS]] >= 0).all().all()
    assert feats.drift_score.between(0, 1).all()


def test_predict_schema(feats):
    r = b.predict(feats.patient_id.iloc[100], feats.day.iloc[100])
    assert isinstance(r, AgentResult) and r.agent == "behaviour"
    assert {"drift_score", "agitation_prob", "drifted_features"} <= set(r.evidence) or "reason" in r.evidence
