"""The TIHM data ECHOS v2 uses: one row per patient-day, labels counted correctly, patient-disjoint splits (skipped in a fresh clone)."""
import numpy as np
import pytest

from echos.data import loaders
from echos.data.splits import patient_folds
from echos.monitor import cohort


@pytest.fixture(scope="module")
def vit():
    return cohort.vitals_cohort()


def test_one_row_per_patient_day(vit):
    assert not vit.duplicated(["patient_id", "day"]).any()
    assert (vit.groupby("patient_id").day.apply(lambda d: d.diff().dropna().dt.days.eq(1).all())).all()    # a calendar: gaps are rows, not holes


def test_inclusion_rule_and_event_labels(vit):
    bp_days = vit[vit.has_reading == 1].groupby("patient_id").size()
    assert bp_days.min() >= 14
    lab = loaders.load_labels()
    lab = lab[lab.type.isin(cohort.VITAL_LABELS) & lab.patient_id.isin(set(vit.patient_id))]
    inside = lab.merge(vit[["patient_id", "day"]], on=["patient_id", "day"])
    assert int(vit.event.sum()) == inside[["patient_id", "day"]].drop_duplicates().shape[0]


def test_splits_are_patient_disjoint(vit):
    seen = []
    for tr, te in patient_folds(vit, 5, 42):
        a, b = set(vit.iloc[tr].patient_id), set(vit.iloc[te].patient_id)
        assert not a & b
        seen += sorted(b)
    assert sorted(seen) == sorted(set(vit.patient_id)) and len(np.unique(seen)) == len(seen)
