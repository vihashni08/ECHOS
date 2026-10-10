import pandas as pd
import pytest

from echos.data import loaders
from echos.data.patient_day import LABELS, load_patient_day
from echos.data.splits import patient_folds


@pytest.fixture(scope="module")
def pdy():
    return load_patient_day()


def test_no_duplicate_patient_day(pdy):
    assert not pdy.duplicated(["patient_id", "day"]).any()


def test_label_counts_match(pdy):
    lab = loaders.load_labels()
    for typ, col in LABELS.items():
        expected = lab[lab.type == typ][["patient_id", "day"]].drop_duplicates()
        assert pdy[col].sum() == len(expected), typ
        got = pdy[pdy[col] == 1][["patient_id", "day"]]
        assert len(got.merge(expected)) == len(expected)


def test_splits_are_patient_disjoint(pdy):
    folds = list(patient_folds(pdy))
    assert len(folds) == 5
    for tr, te in folds:
        assert not set(pdy.iloc[tr].patient_id) & set(pdy.iloc[te].patient_id)
