# ECHOS v2: Integrated Healthcare Monitoring and Decision Support System using Multi Agents
Multi-agent decision support for home monitoring of people living with dementia (TIHM). Not a diagnostic tool; no model decides an emergency.
Method, equations, results and limitations: docs/METHOD.md (the source of truth for numbers). v1 (falls, LLM reports, live ingestion) = git tag v1-before-refactor.

## Data (never cat large files; use pandas nrows or aggregate in scripts)
- data/tihm/Dataset/ (git-ignored): Physiology.csv, Labels.csv (608 rows), Activity.csv (~1M room/door events), Demographics.csv. Sleep.csv is NOT used (17 patients).
- Used: systolic / diastolic / heart rate (vitals cohort = patients with >= 14 BP days: 41 patients, 2,201 calendar days; echos/monitor/cohort.py),
  temperature / body water (alert rules only), Activity -> 8,516 windows (night 18-08, 08-12, 12-18) for 56 patients, labels Blood pressure + Pulse
  (vitals target) and Agitation (135, 27 patients). Not used: skin temperature, weight / temperature / body-water labels (too few), sleep, falls (TIHM has none).
- Labels are alerts from the study's rules / model that its monitoring team confirmed. Label audit (scripts/evaluation/label_audit.py): BP labels follow a
  same-day threshold (88% of days >= 160/100 labelled) -> no same-day vitals "detection" claim; new episodes are ~unpredictable -> no forecasting claim.
- Date range 2019-04-01 to 2019-06-30. Small cohort: report CIs, call weak results weak.

## Methods (echos/monitor/)
- personal_baseline.py: Normal-Inverse-Gamma per person per vital, empirical-Bayes prior from training patients; usual / z / surprise from PAST readings only.
- risk.py: Beta-Bernoulli personal event rate, method-of-moments prior; 3-day risk 1-(1-r)^3, 95% credible interval. The vitals review priority.
- agitation.py: window features + personal same-slot baseline z (prev 14 slots) + episode history -> XGBoost + Platt (fitted on training patients' OOF).
- allocation.py: priority = max(v / v_bar, a / a_bar) (lift over each base rate), fairness floor (unseen >= 7 days, <= half of K), top K.
- cohort.py: urgent_flags = NEWS2 red scores + BP >= 180/120 (URGENT); safety_flags = TIHM Table 5 limits (attention notes only).
- episode.py: two-state HMM (Baum-Welch) kept as an ABLATION (it lost to the Beta rate). store.py: per-fold saved models (models/v2, git-ignored).

## Agents (echos/team/; four, each a separate process under the supervisor; they talk ONLY via the SQLite bus agent_message)
- Patient Monitor (patient_monitor.py, one PatientMonitor per person, memory in table patient_agent; also runs the sensor-silence / stale-vitals rules of echos/monitor/quality.py: a 'data check' note + case_state.data_gap, never a score change): scores 'day' / 'reading' events; red flag -> 'urgent'
  to care; after 'feedback' -> 'review_request' to review_coordinator. Waits for the previous day's recorded outcome before scoring a person's next day.
- Review Coordinator (review_coordinator.py, memory in agent_state): on 'day_close' waits for every request (longer while the monitor is down), allocates
  config review.k_per_day, writes review_list + case status REVIEW / WATCH / NORMAL (never downgrades URGENT), sends 'assign' and 'review_decision'.
- Evidence agent (evidence_agent.py + echos/evidence/): on `evidence_request` from Care (sent AFTER an alert is queued) retrieves guideline passages (TF-IDF over data/knowledge/raw: NICE NG136, RCP NEWS2; add NG97 for agitation), Gemini (ECHOS_LLM_API_KEY, config `evidence:`) writes a cited note from structured reasons + passages only, echos/evidence/validator.py checks numbers / citations / wording, else fixed template; URGENT first; answers cached in data/knowledge/llm_cache.sqlite; tests and e2e set ECHOS_EVIDENCE_LLM=off (never call Gemini). Table evidence_card, API /api/v2/evidence, /api/v2/trace/{pid}/{day}.
- Care agent (care_agent.py + coordinator.py lifecycle): 'urgent' -> fixed-template alert via the dispatcher at once; 'assign' -> CLINICIAN_REVIEW task;
  'outcome' events / 'clinician_outcome' -> 'feedback' to the monitor; escalation NEW->SENT->ACKNOWLEDGED->RESOLVED / ESCALATED / EXPIRED.
- agent_base.py: heartbeat, cursor (agent_checkpoint), memory, wait rule (peer_down / waited_enough). run_agent.py: one agent per process.
- runtime.py: replay controller + supervisor; watchdog (echos/orchestrator/watchdog.py) restarts the runtime; SimController.resume() continues after it.
- Replay (echos/sim/replay_v2.py): real TIHM days for a demo cohort (8 patients x 28 days chosen for events): 'day', then 'outcome' (the study's
  confirmed labels = feedback), then 'day_close'. Each person is scored by the fold model that EXCLUDED them (no in-sample demo).
  The only invented data: POST /api/sim/reading (inject_reading), marked injected.

## Alerts (echos/alerts/)
- URGENT only (red flags): to caregiver (ECHOS_CAREGIVER_EMAIL) and clinician (ECHOS_CLINICIAN_EMAIL), fixed template from structured data, de-identified id +
  age band; outbox with retries (alerts.retry_window_s), sweeper, per-patient cooldown (alerts.critical_cooldown_sim_h), ack link (ECHOS_LINK_SECRET +
  ECHOS_PUBLIC_URL). alerts.send_real defaults to false = DryRunMailer. NEVER send real email from tests or demos: tests use FakeMailer (conftest blocks
  smtplib.SMTP); real mail only with scripts/start.sh --real --yes.

## Evaluation (scripts/evaluation/, all write reports/metrics/v2_*.json; patient-level rows go to git-ignored data/v2/)
- label_audit.py, vitals_eval.py, agitation_eval.py, allocation_eval.py (uses the OOF scores; includes the plain BP-limit rules, which match ECHOS on events per review: 0.471 vs 0.452), team_e2e.py (real processes + SIGKILL kill test), demo_data.py (audit grid, budget curves, You-vs-ECHOS days; run after the evals), evidence_eval.py (gold set evidence_gold.json, hand labels evidence_labels.json; calls Gemini once, answers cached), quality_eval.py (sensor-silence firing rates).
- scripts/train_v2.py -> models/v2 (per-fold + 'all' models, base rates). scripts/rebuild_models.sh runs everything in order.
- Protocol: 5-fold GroupKFold BY PATIENT (echos/data/splits.py), everything fitted on training patients only, 1 alert / patient-week budget, PR-AUC primary,
  1000 patient-level bootstrap CIs, paired bootstrap for differences ("better" only if the whole CI > 0).

## API and web
- echos/api (FastAPI, scripts/run_api.sh [--dry] [--demo] [--auth] [--db PATH]): 127.0.0.1 only, CORS http://localhost:5173, auth optional (roles
  caregiver < clinician < operator). Never loads a model: reads SQLite + metrics, commands via sim_command, SSE /api/events from event_feed triggers.
  v2 endpoints: /api/v2/day (the Daily review: one day, every patient, all four agents), /api/v2/review, /api/v2/patients/{id}/monitor, /api/v2/reviews/outcome, /api/v2/summary, /api/sim/reading. docs/API.md.
- web/ (Vite + React + TS + Tailwind v4 + shadcn/ui + Recharts + TanStack Query): menu = Daily review (/, the demo: readings vs usual, top K with reasons +
  evidence cards, urgent alerts), Patients, Report & references (/report; refs in web/src/lib/refs.tsx, cited with <Cite n/>), System (/system); older pages
  (/briefing, /trace, /test, /review, /alerts, /how) stay reachable but are not in the menu; Demo tools
  sheet (replay controls, inject a reading, kill an agent). Typed client: cd web && npm run gen:api. Charts: one y-axis, dashed = personal usual,
  colours --chart-1/2 (validated palette), red-flag markers always labelled.
- One command: scripts/start.sh (fresh data/echos_demo.db, pre-fills 21 of 28 days then pauses at 5 ev/s, dry-run mail, --empty / --keep / --auth /
  --real --yes / --check). --check uses data/echos_check.db + data/logs/check/ so tests never wipe a running demo. Demo script: docs/DEMO.md.
- Checks: PYTHONPATH=. .venv/bin/python -m pytest -q; cd web && npm run build && node contrast.mjs && npx playwright test (own stack :8010/:5180,
  axe WCAG AA both themes) && npx playwright test -c playwright.auth.config.ts (:8031/:5201).

## Rules
- Python 3.11, pydantic, pandas, scikit-learn, xgboost, fastapi, sqlalchemy/sqlite, pytest.
- Split by PATIENT (GroupKFold). Never random rows. Personal baselines use only past data.
- Report PR-AUC, recall / precision at a fixed alert budget, false alerts per patient-week, CIs: not accuracy. Honest results: weak = weak.
- Models train only on real data: training tables carry source='tihm' and trainers call echos.data.guard.assert_real_source.
- Agents never call each other directly: bus messages only. All agent work idempotent; state persisted; URGENT never waits for anything.
- Patient-level artefacts (data/, models/, *.db, *.parquet, web/screenshots) are git-ignored; publish aggregates only. Secrets never printed.
- Keep output short. Do not print DataFrames over 10 rows. Commit after each finished phase.
- Layout: scripts/evaluation (evals), scripts/docgen (OpenAPI export, components doc), shell scripts in scripts/. Run with PYTHONPATH=. python scripts/<group>/<name>.py.
