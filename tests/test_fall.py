import numpy as np

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


def test_peak_window_same_path_for_falls_and_adls():
    a = np.zeros((2000, 3)); a[:, 2] = 1.0; a[900, 0] = 6.0
    for jit in (-100, 0, 100):
        w, pk, s = fall.peak_window(a, 500, jit)
        assert pk == 900 and len(w) == 500 and np.argmax(sf.svm(w)) == 250 - jit
    assert fall.peak_window(a, 500, 0)[0].shape == (500, 3)
    a[900, 0] = 0.0; a[100, 0] = 6.0
    assert fall.peak_window(a, 500, 0) is None                    # peak too close to the start: skipped identically for both classes


def test_v2_windows_have_no_peak_position_leak():
    from sklearn.metrics import roc_auc_score
    df = fall.build_windows_v2()
    m = df.kind.isin(["fall", "adl_peak"])
    assert abs(roc_auc_score(df[m].y, -(df[m].peak_pos - 250).abs()) - 0.5) < 0.05
    assert df[df.kind == "fall"].peak_pos.sub(250).abs().max() <= 100 and df[df.kind == "adl_peak"].peak_pos.sub(250).abs().max() <= 100
    assert set(df.source) == {"sisfall"} and {"fall", "adl_peak", "adl_slide"} == set(df.kind)
