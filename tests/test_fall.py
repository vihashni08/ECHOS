import numpy as np
import pandas as pd

from echos.agents import fall
from echos.data import sisfall as sf
from echos.data.splits import subject_folds
from echos.schemas import AgentResult


def test_subject_split_has_no_overlap():
    df = fall.build_windows()
    for tr, te in subject_folds(df, 5, 42):
        assert not set(df.iloc[tr].subject) & set(df.iloc[te].subject)


def test_fall_window_centred_on_peak():
    a = np.zeros((1000, 3)); a[:, 2] = 1.0; a[400, 0] = 5.0
    w = sf.fall_window(a, 500)
    assert len(w) == 500 and np.argmax(sf.svm(w)) == 250


def test_predict_window_schema_and_inactivity():
    a = np.zeros((500, 3)); a[:, 2] = 1.0; a[250, 0] = 4.0
    still = np.tile([0, 0, 1.0], (800, 1)) + np.random.RandomState(0).normal(0, 0.01, (800, 3))
    r = fall.predict_window(a, following=still)
    assert isinstance(r, AgentResult) and r.evidence["post_fall_inactive"] is True
    moving = np.random.RandomState(1).normal(0, 0.5, (800, 3))
    assert fall.predict_window(a, following=moving).evidence["post_fall_inactive"] is False


def test_conversion_to_g():
    assert abs(256 * sf.G_PER_COUNT - 1.0) < 1e-9
