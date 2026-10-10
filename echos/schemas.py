from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TriageStatus(str, Enum):
    NORMAL = "NORMAL"
    MONITOR = "MONITOR"
    URGENT = "URGENT"


class AgentResult(BaseModel):
    patient_id: str
    day: date
    agent: str
    score: float = Field(ge=0.0, le=1.0)
    flag: bool
    evidence: Dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=datetime.utcnow)


class DataQualityResult(BaseModel):
    """Agent 0 output for one patient-day (rule-based; carried in AgentResult.evidence for the agent runtime)."""
    patient_id: str
    day: date
    has_vitals: bool                                  # a BP/HR reading exists that day (after range checks)
    invalid_values: List[Dict[str, Any]] = Field(default_factory=list)  # [{device_type, value, range}] set to NaN, never imputed
    sensor_gap: bool = False                          # activity sensor silent / abnormally low: NOT "zero activity"
    sensor_gap_reason: Optional[str] = None           # zero_events | low_events | no_activity_record
    days_since_last_reading: Optional[int] = None     # 0 = reading today; counted from the patient's first record if never read
    completeness: float = Field(default=1.0, ge=0.0, le=1.0)
    quality_checked: bool = True                      # False = Agent 0 failed, data passed through unchecked
