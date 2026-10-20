"""ONE source of truth for the daily-report reply shape: the prompt text, Gemini's structured-output schema and the strict parse all come from
`DailyReport`. Anything that does not fit (wrong key, plain strings instead of {text, cites}, empty text, missing list) is a `schema_mismatch`."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, pattern=r"\S", description="2 to 4 plain-language sentences naming the exact measure, its recorded value and time, the person's own usual and the direction and size of the difference")
    cites: list[str] = Field(description="flag ids (F1) or retrieved source ids (KB-12, PM-123); [] when nothing applies")


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: Literal["NOW", "TODAY", "WITHIN_48H", "THIS_WEEK"] = Field(description="NOW = within the hour, TODAY, WITHIN_48H, THIS_WEEK")
    owner: Literal["CAREGIVER", "CLINICIAN"]
    text: str = Field(min_length=1, pattern=r"\S", description="what to do, how and when, naming the exact measure and its recorded value; one or two sentences")
    escalate_if: str = Field(description="when this needs more than the plan: the sign or limit and the step to take; \"\" when not applicable")
    cites: list[str] = Field(description="flag ids (F1) or retrieved source ids (KB-12, PM-123); [] when nothing applies")


class DailyReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["NORMAL", "MONITOR", "URGENT"]
    assessment: list[Item] = Field(min_length=1, max_length=2, description="1 or 2 short paragraphs that tie the findings together: what the day looks like overall and what matters most, in plain words, without diagnosing")
    changes: list[Item] = Field(description="what differs from this person's own usual pattern, one item per flag")
    actions: list[Action] = Field(min_length=1, max_length=5, description="the plan, most urgent first")
    guidance: list[Item] = Field(max_length=2, description="at most 2 paraphrased statements citing retrieved source ids; [] if none")


def json_schema() -> dict:
    """Plain JSON Schema (inlined, no $ref) for Gemini's `response_json_schema` and for the prompt."""
    s = DailyReport.model_json_schema()
    defs = s.pop("$defs", {})

    def inline(x):
        if isinstance(x, dict):
            if "$ref" in x:
                return inline(defs[x["$ref"].split("/")[-1]])
            return {k: inline(v) for k, v in x.items() if k not in ("title", "pattern", "additionalProperties")}
        return [inline(v) for v in x] if isinstance(x, list) else x
    return inline(s)


def prompt_shape() -> str:
    return json.dumps(json_schema(), separators=(",", ":"))


def parse(out) -> tuple[DailyReport | None, list[str]]:
    """Strict parse -> (model, []) or (None, errors). Errors name the path and the problem, never the text."""
    try:
        return DailyReport.model_validate(out), []
    except ValidationError as ex:
        errs = [f"schema mismatch at {'.'.join(str(p) for p in e['loc']) or 'root'}: {e['type']}" for e in ex.errors()]
        return None, sorted(set(errs))[:8]
