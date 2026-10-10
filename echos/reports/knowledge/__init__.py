from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).with_name("guidelines.json")
_DB: dict = {}


def get_guideline(finding_type: str) -> list:
    """Keyed lookup (no vector DB). Unknown keys return []. Every snippet is for clinician review only."""
    if not _DB:
        _DB.update(json.loads(_PATH.read_text()))
    return [dict(s, note="for clinician review") for s in _DB.get(finding_type, [])]


def finding_types() -> list:
    get_guideline("")
    return sorted(_DB)
