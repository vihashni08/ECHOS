import datetime as dt

import pytest
from pydantic import ValidationError

from echos.schemas import AgentResult, TriageStatus


def test_agent_result_ok():
    r = AgentResult(patient_id="a", day=dt.date(2019, 4, 1), agent="physiological", score=0.3, flag=False)
    assert r.evidence == {} and r.ts


def test_score_bounds():
    with pytest.raises(ValidationError):
        AgentResult(patient_id="a", day=dt.date(2019, 4, 1), agent="x", score=1.5, flag=True)


def test_triage_enum():
    assert {s.value for s in TriageStatus} == {"NORMAL", "MONITOR", "URGENT"}
