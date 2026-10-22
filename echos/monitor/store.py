"""Saved v2 models for the runtime (models/v2/, git-ignored; built by scripts/train_v2.py).

For every patient the runtime uses the models of the cross-validation fold that did NOT contain that patient, so a replay demo scores each
person with a model that never saw them (the same out-of-fold setting as the evaluation). A patient the training data never contained (live
mode) gets the model trained on everyone ('all')."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import joblib

from echos.config import ROOT
from echos.monitor.personal_baseline import NIGPrior
from echos.monitor.risk import BetaPrior

DIR = ROOT / "models" / "v2"


@dataclass
class PatientModels:
    fold: str                 # "0".."4" or "all"
    nig: dict                 # vital -> NIGPrior
    beta: BetaPrior
    agitation: object         # AgitationModel


@lru_cache(maxsize=1)
def _meta(path: str = str(DIR)) -> dict:
    p = Path(path) / "meta.json"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: run PYTHONPATH=. python scripts/train_v2.py")
    return json.loads(p.read_text())


@lru_cache(maxsize=16)
def _load_fold(fold: str, path: str = str(DIR)) -> tuple:
    m = _meta(path)["folds"][fold]
    nig = {v: NIGPrior(**p) for v, p in m["nig"].items()}
    beta = BetaPrior(m["beta"]["a0"], m["beta"]["b0"])
    ag = joblib.load(Path(path) / f"agitation_{fold}.joblib")
    return nig, beta, ag


def for_patient(patient_id: str, path: str = str(DIR)) -> PatientModels:
    """Vitals and agitation folds are separate splits (different cohorts); each part uses the fold that excluded the patient."""
    m = _meta(path)
    fv = str(m["vitals_fold_of"].get(patient_id, "all"))
    fa = str(m["agitation_fold_of"].get(patient_id, "all"))
    nig, beta, _ = _load_fold(fv, path)
    _, _, ag = _load_fold(fa, path)
    return PatientModels(fold=f"vitals:{fv} agitation:{fa}", nig=nig, beta=beta, agitation=ag)


def base_rates(path: str = str(DIR)) -> dict:
    """Population means used by the Review Coordinator's lift: v_bar (3-day vitals forecast) and a_bar (daily max agitation probability)."""
    return _meta(path)["base_rates"]


def clear_cache() -> None:
    _meta.cache_clear()
    _load_fold.cache_clear()
