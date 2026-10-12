"""Pseudonymisation for LLM prompts: the TIHM patient id never leaves the machine; the model sees ALIAS, code maps it back."""
from __future__ import annotations

ALIAS = "Patient A"


def pseudonymise(x, pid: str):
    """Deep copy of `x` with the patient id replaced by ALIAS in every string (dict keys too)."""
    if isinstance(x, str):
        return x.replace(pid, ALIAS) if pid else x
    if isinstance(x, dict):
        return {pseudonymise(k, pid): pseudonymise(v, pid) for k, v in x.items()}
    if isinstance(x, list):
        return [pseudonymise(v, pid) for v in x]
    return x


def pseudonymise_back(x, pid: str):
    """Map ALIAS in the model's output back to the patient id (code only)."""
    if isinstance(x, str):
        return x.replace(ALIAS, pid)
    if isinstance(x, dict):
        return {k: pseudonymise_back(v, pid) for k, v in x.items()}
    if isinstance(x, list):
        return [pseudonymise_back(v, pid) for v in x]
    return x
