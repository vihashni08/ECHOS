"""List the Gemini model names the API key can use for content generation (SDK model-listing call, google-genai `client.models.list()`).
Prints names only (never the key). Makes ONE listing request; refuses to run with ECHOS_REPORT_LLM=off.
Usage: PYTHONPATH=. python scripts/realservices/list_models.py"""
import os
import sys

from echos.config import ROOT


def generation_models(client) -> list[str]:
    """Names (without the 'models/' prefix) of models whose supported actions include generateContent."""
    out = []
    for m in client.models.list():
        if "generateContent" in (getattr(m, "supported_actions", None) or []):
            out.append((m.name or "").removeprefix("models/"))
    return sorted(set(out))


def main() -> int:
    if os.environ.get("ECHOS_REPORT_LLM") == "off":
        print("ECHOS_REPORT_LLM=off: not listing models.")
        return 2
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from echos.reports.llm.llm import ENV_KEY
    if not os.environ.get(ENV_KEY):
        print("No ECHOS_LLM_API_KEY in the environment / .env.")
        return 1
    from google import genai
    for n in generation_models(genai.Client(api_key=os.environ[ENV_KEY])):
        print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
