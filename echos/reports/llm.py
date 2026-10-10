"""Optional Gemini client (official `google-genai` SDK). Returns None when no key or no model is configured:
the Report agent then runs in template mode. The key is read from the env var ECHOS_LLM_API_KEY (via .env) and is
never printed or logged."""
from __future__ import annotations

import os

from echos.config import ROOT, load_config

ENV_KEY = "ECHOS_LLM_API_KEY"


class GeminiLLM:
    def __init__(self, api_key: str, model: str, temperature: float = 0.2):
        from google import genai
        self._client = genai.Client(api_key=api_key)
        self.model, self.temperature = model, temperature

    def generate(self, system: str, user: str) -> str:
        from google.genai import types
        r = self._client.models.generate_content(
            model=self.model, contents=user,
            config=types.GenerateContentConfig(system_instruction=system, temperature=self.temperature,
                                               response_mime_type="application/json"))
        return r.text or ""


def make_llm(cfg: dict | None = None):
    cfg = cfg or load_config()
    model = (cfg.get("llm") or {}).get("model")
    if not model:
        return None
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key = os.environ.get(ENV_KEY)
    if not key:
        return None
    return GeminiLLM(key, model, (cfg.get("llm") or {}).get("temperature", 0.2))
