"""Offline switches. force_offline() makes a process unable to reach Gemini, PubMed or a real SMTP server, whatever .env / config say."""
import os


def force_offline() -> None:
    os.environ["ECHOS_REPORT_LLM"] = "off"
    os.environ["ECHOS_PUBMED"] = "off"
    os.environ["ECHOS_DRY_RUN"] = "1"


def llm_off() -> bool:
    return os.environ.get("ECHOS_REPORT_LLM") == "off"
