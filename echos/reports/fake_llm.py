"""FakeLLM for tests and offline demos: replays recorded / scripted responses, never touches the network.

A script is a list of responses, consumed one per request:
  {"text": "..."}                                  final answer
  {"calls": [{"name": "...", "args": {...}}]}      function calls
  {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}   raise LLMAPIError
  {"error": "timeout"}                             raise LLMTimeout
  {"error": "boom"}                                raise RuntimeError
A response may also be an exception instance or a callable (system, messages, tools) -> response dict.
When the script runs out the last response repeats (so a hostile model keeps being hostile).
`FakeLLM.recorded(name)` loads tests/recordings/<name>.json (a JSON list of such responses).
Both interfaces exist: `step` (daily report, function calling) and `generate` (weekly report: next response's text)."""
from __future__ import annotations

import json

from echos.config import ROOT
from echos.reports.llm import LLMAPIError, LLMTimeout, Step


class FakeLLM:
    def __init__(self, script=None, model: str = "fake-model"):
        self.script = list(script or [])
        self.model = model
        self.requests: list = []      # one entry per request: {"messages": n, "tools": [names] | None}
        self.calls = 0
        self.prompts: list = []       # everything sent per request, as one JSON string (system + messages): tests check what the model would see

    @classmethod
    def recorded(cls, name: str, model: str = "fake-model") -> "FakeLLM":
        return cls(json.loads((ROOT / "tests" / "recordings" / f"{name}.json").read_text()), model)

    def _next(self, system, messages, tools):
        self.calls += 1
        self.prompts.append(json.dumps({"system": system, "messages": messages}, default=str))
        self.requests.append({"messages": len(messages), "tools": [t["name"] for t in tools] if tools else None})
        r = self.script.pop(0) if len(self.script) > 1 else (self.script[0] if self.script else {"text": ""})
        if callable(r):
            r = r(system, messages, tools)
        if isinstance(r, BaseException):
            raise r
        e = r.get("error")
        if e == "timeout":
            raise LLMTimeout("fake: no response within 45s")
        if isinstance(e, dict):
            raise LLMAPIError(e["code"], e.get("status", "ERROR"), e.get("message", "fake error"))
        if e:
            raise RuntimeError(str(e))
        return r

    def step(self, system, messages, tools=None, schema=None) -> Step:
        r = self._next(system, messages, tools)
        return Step(text=r.get("text", ""), calls=list(r.get("calls", [])), prompt_tokens=r.get("prompt_tokens", 100), output_tokens=r.get("output_tokens", 50))

    def generate(self, system, user) -> str:
        r = self._next(system, [{"role": "user", "text": user}], None)
        return r.get("text", "")
