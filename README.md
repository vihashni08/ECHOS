# ECHOS v2

**Evidence-Centred Health Observation and Support through collaborative AI agents.**

ECHOS is a decision-support system for home monitoring of people living with dementia, built on the public TIHM dataset. It is not a diagnostic tool, and no model decides an emergency.

Four cooperating agents run as separate processes and talk only through a message bus:

- **Patient Monitor (one per person).** Learns what is usual for that person: blood pressure, pulse, and room activity by time of day. It raises an **URGENT** message on a red-flag reading. After each day's outcome it asks for a clinician review, with its risks and plain-words reasons.
- **Review Coordinator.** Shares a small team's **K reviews per day** across everyone. Priority is how far each risk is above its base rate, with a fairness floor so nobody goes unseen for more than a week.
- **Care agent.** Delivers URGENT alerts at once, escalates when nobody acknowledges, opens review tasks, and sends the clinician's outcome back to the Patient Monitor, which learns from it.
- **Evidence agent.** Gives every urgent alert and review card a short explanation that cites a guideline passage (NICE NG136, RCP NEWS2) found by a local index. Gemini writes the wording from the retrieved passages only; a non-LLM validator checks it; a fixed template is used if anything fails. It never delays an alert.

## Results
5-fold cross-validation split by patient, with patient-level bootstrap 95% CIs. Full method, equations and limitations: [docs/METHOD.md](docs/METHOD.md).

| Part | Result | Chance / comparison |
|---|---|---|
| Agitation detection (activity sensors) | PR-AUC 0.27 (0.20–0.35), ROC 0.96; 88 of 135 episodes at 1 alert / patient-week | chance 0.016 |
| BP / pulse review priority (personal Bayesian rate) | PR-AUC 0.62 (0.43–0.72) | chance 0.28; significantly above the study's own threshold rule (+0.050) |
| Review Coordinator, 4 reviews / day | 0.45 confirmed events per review (follows up 61% of agitation episodes) | random 0.25; a plain BP-limit rule gets 0.47 but follows up 8% of agitation |
| Evidence agent (cited explanations) | retrieval hit@3 1.00 on 31 hand-built cases, 6/6 correct abstentions; validated Gemini text 26/31, of which 22 fully supported, 4 partly, 0 unsupported | naive retrieval hit@3 0.55; small, single annotator |
| URGENT alert, reading → queued | 0.29 s median | killing an agent: 0 lost, 0 extra alerts |

**Label audit.** 88% of days with BP ≥ 160/100 carry a BP label. The labels are confirmed threshold alerts, so ECHOS does not claim same-day vitals "detection" or forecasting of new episodes.

## Quick start (macOS / Linux)
You need Python 3.11, Node 20+ and the TIHM files in `data/tihm/Dataset/` (see [docs/DATA_STATEMENT.md](docs/DATA_STATEMENT.md)).

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
cd web && npm ci && cd ..
```

```bash
scripts/rebuild_models.sh
```

```bash
scripts/start.sh
```

`scripts/rebuild_models.sh` writes `reports/metrics/v2_*.json` and `models/v2/` (a few minutes). `scripts/start.sh` starts the API, the runtime and the web app; open the printed URL.

In the web app, open **Demo tools** and press **Play**. The demo replays 8 TIHM patients over 28 days, and each person is scored by a model that never saw them. Mail is a dry run (outbox only) unless `scripts/start.sh --real --yes`.

Demo script: [docs/DEMO.md](docs/DEMO.md).

## Reproduce the numbers
```bash
scripts/rebuild_models.sh
```

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
```

```bash
cd web && npx playwright test
```

The rebuild script runs, in order:
1. `label_audit.py`
2. `vitals_eval.py`
3. `agitation_eval.py`
4. `allocation_eval.py`
5. `train_v2.py`
6. `team_e2e.py` (real agent processes plus a kill test)

The Playwright suite starts its own stack on ports 8010 / 5180 and checks every page, accessibility (WCAG AA) in both themes, the inject → URGENT → acknowledge flow and an agent crash.

## Layout
| Path | What |
|---|---|
| `echos/monitor/` | Methods: `cohort` (data used), `personal_baseline` (NIG), `risk` (Beta event rate), `agitation`, `allocation`, `episode` (HMM ablation), `metrics`, `store` (saved models) |
| `echos/team/` | Agents: `patient_monitor`, `review_coordinator`, `care_agent` (+ `coordinator` alert lifecycle), `bus`, `agent_base`, `run_agent`, `runtime` |
| `echos/evidence/` | Evidence agent's tools: `corpus` (guideline index, TF-IDF), `llm` (Gemini client, FakeLLM), `explain` (retrieve, write, check), `validator` |
| `echos/alerts/` | URGENT alert payload (fixed template), dispatcher (outbox, retries), mailers, acknowledgement link |
| `echos/sim/` | Real-data replay (`replay_v2`) and its controller |
| `echos/orchestrator/` | SQLite state, supervisor (restarts agents), watchdog (restarts the runtime) |
| `echos/api/` | FastAPI: reads SQLite only, never loads a model ([docs/API.md](docs/API.md)) |
| `web/` | React app: Morning briefing, Decision trace, Test it yourself, Review board, Patients, Alerts, Under the hood, How it works, Presenter mode |
| `scripts/evaluation/` | The evaluation scripts above |
| `docs/` | METHOD (method + results), ARCHITECTURE, COMPONENTS (generated), DEMO, API, DATA_STATEMENT, REPORT_DRAFT |

Version 1 (fall detection on SisFall, the LLM report agent, live device ingestion) is kept in git history under the tag `v1-before-refactor`.
