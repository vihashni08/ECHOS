"""Multi-agent layer. Detectors (echos/agents) sense; the agents here decide how to act on a case:
  Triage agent (echos/orchestrator)  rules -> status           Case Investigator (investigator.py)  builds the case file
  Care Coordinator (coordinator.py)  alert lifecycle           Report agent (echos/reports)         wording (LLM optional)
They talk through the SQLite message bus (bus.py), so every hand-over is visible in the UI."""
