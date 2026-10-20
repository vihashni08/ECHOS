import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    # explicit per-run opt-ins for a real-services demo (scripts/start.sh --week-demo --real --yes); config.yaml keeps the safe defaults
    if os.environ.get("ECHOS_SEND_REAL") == "1" and os.environ.get("ECHOS_DRY_RUN") != "1":
        cfg["alerts"]["send_real"] = True
    if os.environ.get("ECHOS_WEEKLY_LLM") == "1":
        cfg["weekly"]["llm_narrative_in_simulation"] = True
    if os.environ.get("ECHOS_CRITICAL_COOLDOWN_H"):
        cfg["alerts"]["critical_cooldown_sim_h"] = float(os.environ["ECHOS_CRITICAL_COOLDOWN_H"])
    return cfg
