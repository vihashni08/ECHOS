"""Ingestion contract: what a device, hub or emulator sends. One schema for all sources."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

VITAL_TYPES = ["Systolic blood pressure", "Diastolic blood pressure", "Heart rate", "Body Temperature", "Body weight"]
ROOMS = ["Kitchen", "Hallway", "Lounge", "Bedroom", "Fridge Door", "Bathroom", "Front Door", "Back Door"]


class IngestEvent(BaseModel):
    patient_id: str = Field(min_length=1, max_length=64)
    device_id: str = Field(default="device-1", max_length=64)
    ts: datetime                                             # DATA time (when it happened). Naive = local time of the patient.
    kind: Literal["activity", "vitals", "accel", "heartbeat"]
    room: Optional[str] = None                               # activity: one of ROOMS
    device_type: Optional[str] = None                        # vitals: one of VITAL_TYPES
    value: Optional[float] = None                            # vitals
    unit: Optional[str] = None
    fs: Optional[int] = Field(default=None, ge=10, le=1000)   # accel: sampling rate in Hz
    samples: Optional[list[list[float]]] = None              # accel: [[x, y, z], ...] in g; the impact window plus what followed it
    source: Literal["device", "hub", "emulator"] = "device"

    @model_validator(mode="after")
    def _kind_fields(self):
        if self.kind == "activity" and self.room not in ROOMS:
            raise ValueError(f"activity needs room in {ROOMS}")
        if self.kind == "vitals" and (self.device_type not in VITAL_TYPES or self.value is None):
            raise ValueError(f"vitals needs device_type in {VITAL_TYPES} and a value")
        if self.kind == "accel":
            if not self.samples or not self.fs:
                raise ValueError("accel needs samples and fs")
            if len(self.samples) > 6000 or any(len(s) != 3 for s in self.samples):
                raise ValueError("accel samples: at most 6000 rows of [x, y, z]")
        return self

    @field_validator("patient_id", "device_id")
    @classmethod
    def _plain(cls, v: str) -> str:
        if not all(ch.isalnum() or ch in "-_." for ch in v):
            raise ValueError("ids may contain letters, digits, - _ . only")
        return v


class IngestBatch(BaseModel):
    events: list[IngestEvent] = Field(min_length=1, max_length=500)


class IngestResult(BaseModel):
    accepted: int
    duplicates: int
    rejected: int = 0
    patients: list[str]
