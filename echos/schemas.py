from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict

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
