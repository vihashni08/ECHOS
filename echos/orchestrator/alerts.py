"""Immediate alerts: fixed template text, no LLM, no lifecycle. One row per event (patient|day|type), status always 'SENT'.

Fall and critical-vitals alerts go to caregiver AND clinician at once. (Delivery channel is out of scope here: the row
is the record that the alert was handed to the sender.)
"""
from __future__ import annotations

from echos.orchestrator import state as S

RECIPIENTS = ["caregiver", "clinician"]
TEMPLATES = {
    "fall": "ECHOS alert: a possible fall was detected for patient {patient_id} on {day}. Please check on them now.",
    "critical_vitals": "ECHOS alert: a critical vitals reading was recorded for patient {patient_id} on {day}. Please check on them now.",
}


def alert_id(patient_id: str, day: str, type_: str) -> str:
    return f"{patient_id}|{day}|{type_}"


def message(type_: str, patient_id: str, day: str) -> str:
    return TEMPLATES[type_].format(patient_id=patient_id, day=day)


def send_alerts(eng, patient_id: str, day: str, urgent_types: list, reasons: list) -> int:
    """Idempotent: a resumed run cannot create a second row for the same event. Returns rows newly written."""
    n = 0
    with eng.begin() as c:
        for t in dict.fromkeys(urgent_types):
            n += S.insert_ignore(c, S.alerts, alert_id=alert_id(patient_id, day, t), patient_id=patient_id, type=t,
                                 time=S.now(), status="SENT",
                                 payload=S.j({"day": day, "message": message(t, patient_id, day), "recipients": RECIPIENTS,
                                              "reasons": reasons}))
    return n
