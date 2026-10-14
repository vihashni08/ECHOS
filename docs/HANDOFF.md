# ECHOS handoff: where the project stands and what to build next

Written at tag `echos-v3.1` (commit `89451a6` plus the CLAUDE.md fix). Read `CLAUDE.md` first (project rules, architecture, commands), then this file. Everything below is checked against the code and the last full test run.

## 1. What ECHOS is

Decision support (not diagnosis) for older people living at home. It watches room sensors, vital signs and accelerometer data and tells the caregiver and clinician (a) **at once** when a fall or a critical reading is detected and (b) **daily** whether anything differs from that person's own usual, with evidence. A language model never decides an emergency; plain rules do.

## 2. Current architecture (one paragraph per layer)

- **Detectors** (`echos/agents`): Data Quality gate (rules), Vitals + Behaviour (XGBoost + SHAP, personal past-only baselines), Fall (random forest on SisFall windows + post-fall inactivity). They sense and never decide.
- **Agents**: Triage (pure if-then `echos/orchestrator/triage.py`; LangGraph in replay, `echos/live/processor.py` in live mode), **Case Investigator** (`echos/team/investigator.py`, bounded tool loop, case file + trace), **Care Coordinator** (`echos/team/coordinator.py`, `lifecycle.py`: NEW → SENT → ACKNOWLEDGED → RESOLVED, ESCALATED, EXPIRED; check-in and review tasks), **Report agent** (`echos/reports`, LLM optional + non-LLM validator, uses the case file). Agents talk over the SQLite bus (`echos/team/bus.py`). Registry: `echos/components.py` (→ `docs/COMPONENTS.md`, `GET /api/components`, UI "How it works").
- **Three data modes**, always labelled in the UI: REPLAY (TIHM day by day), EMULATOR (`echos/live/emulator.py`, real TIHM raw events in wall-clock time), LIVE (real devices → `POST /api/ingest/events`). In live mode detectors are scored from raw events (`echos/live/features.py`); vitals on arrival, behaviour when the day closes; open days are provisional.
- **Alerts** (`echos/alerts`): fixed template, no LLM, queued before any report work, outbox with retries, dedup, caregiver + clinician; escalation contact `ECHOS_ESCALATION_EMAIL`. `alerts.send_real: false` = dry run by default.
- **Runtime**: `scripts/run_api.sh [--mode live] [--dry] [--no-llm] [--demo] [--auth] --db PATH` starts the API, the **watchdog** (`echos/orchestrator/watchdog.py`) and the runtime (replay controller or live runtime). Live restart: `LiveProcessor.recover()` + `Emulator.resume()`.
- **API** (`echos/api`): FastAPI, 127.0.0.1, CORS for `http://localhost:5173`, **optional** auth (`--auth`, roles caregiver < clinician < operator, tokens from `ECHOS_TOKEN_*` env only), ingest token `ECHOS_INGEST_TOKEN` (open when unset).
- **Web** (`web/`): React + TS + Tailwind v4 + shadcn + Recharts. Pages: Today, Alerts, People (with case file), Fall Monitor, Caregiver report, Clinician week, How it works, System; mode badge on every page; Demo tools sheet; replay Demo guide.

## 3. Verified state at `echos-v3.1`

- 30 Python test files pass, **each run in its own process**: `for f in tests/test_*.py; do ECHOS_REPORT_LLM=off ECHOS_PUBMED=off ECHOS_DRY_RUN=1 .venv/bin/python -m pytest "$f" -q; done`
- Playwright: replay `npx playwright test` (10), live `npx playwright test -c playwright.live.config.ts` (5), auth `npx playwright test -c playwright.auth.config.ts` (3). `npx tsc -b`, `npm run build`, `node contrast.mjs`, `python scripts/check_docs_numbers.py` all pass. (Run from `web/` for the web checks; Python from the repo root with `.venv/bin/python`.)
- Offline metrics from scripts: `reports/metrics/live_parity.json` (live == replay scores), `live_faults.json` (fault scenarios, ~0.15–0.19 s event→alert queued, software only), `threshold_holdout.json` (held-out threshold check).

## 4. Known facts and limits you must not paper over (details: `docs/LIMITATIONS.md`)

- TIHM labels are 6-hour clinician-verified events we did not verify; about 49 labelled patients; wide CIs. Falls are SisFall windows (one elderly subject).
- MONITOR share is 28.8% in replay and **threshold changes cannot fix it** (data gaps drive it; held-out check says so). The UI separates "Data needs checking" from "Changed from their usual" instead.
- The emulator is a labelled stand-in, not a device. No real device exists.
- The Investigator and Coordinator are rule-driven agents (no LLM).
- **The real-Gemini path is unverified against the new structured-output schema.** The only live attempt (`scripts/live_report_check.py --yes`) got `504 DEADLINE_EXCEEDED` from Google on the primary model and stopped (2 Gemini + 2 PubMed requests used). **Do not call Gemini again unless the user asks.**
- **The report rating study is not going to be run** (the user cannot recruit readers). Do not propose it again; the kit stays in the repo (`scripts/make_rating_sheet.py`, `analyse_ratings.py`) for later.
- **Real email has been tested by the user** with the credentials already configured in `.env`. Agents must still never send real email from tests or demos and never open or print `.env`.

## 5. Working rules for the next session (from the user)

- Read `CLAUDE.md`. Keep output short. Commit after each finished phase; tag at the end.
- No foreground step over ~3 minutes: run long jobs in the background and poll with a Monitor (no `sleep` chains).
- No Gemini, no PubMed, no real email: use `ECHOS_REPORT_LLM=off ECHOS_PUBMED=off ECHOS_DRY_RUN=1`, `FakeLLM`, `FakeMailer`. Never print secrets. Do not retrain models.
- Tell the user the plan before big work; report honestly (weak results stay weak).
- Use `.venv/bin/python`. Use `PYTHONPATH=.` for scripts.

## 6. NEXT PHASE (what to build, in this order, one commit each)

1. **Docs and screenshots.** Fix any remaining stale statements (`CLAUDE.md` was fixed for "no lifecycle / no auth"; check README, docs/ARCHITECTURE.md, docs/DEMO.md, docs/API.md for "Dashboard" and old page names). Regenerate `web/screenshots/` (`node scripts/ui_screenshots.mjs`; update the script for the new pages Today, Alerts, People, How it works and for live mode).
2. **Acknowledge from the e-mail.** Alert e-mails carry a signed, expiring "I have seen this" link (HMAC over alert id + expiry with a server secret from the environment, e.g. `ECHOS_LINK_SECRET`; base URL `ECHOS_PUBLIC_URL`). `GET /api/ack/{token}` acknowledges without login, shows a small confirmation page, is idempotent, and works with auth on. No patient data in the link; only the alert id. The alert template (`echos/alerts/payload.py`) stays fixed and LLM-free; add the link as one structured line. Tests: valid, expired, forged, wrong alert, reused, auth on, missing secret (link omitted, never a broken link). FakeMailer only.
3. **Real-device readiness.** `docs/INGESTION.md` (event format, error codes, retries, idempotency, security, clock rules); `scripts/device_client.py` reference client (batching, retry with backoff, offline buffer file); server-side limits on `POST /api/ingest/events` (batch size, per-device rate, optional registered device list `ingest.devices` in config, clear 413/429 errors); tests for each limit.
4. **One-command start.** `scripts/start.sh [--live|--replay] [--auth]` starts API + watchdog + runtime + Vite, waits for a health check, prints only URLs (never tokens), stops everything on Ctrl-C. Never touches real services (dry-run mail, no LLM, no PubMed unless the user passes flags).
5. **Data retention.** An option to archive raw `event_log` rows older than N days into a compressed file (keep `live_day`, `case_state`, `case_file`, `alerts`, `reports`) with a dry-run flag, a tests-only fake clock, and a guard that never archives the open day. Document the restart-recovery interaction (`agent_checkpoint` 'live').
6. **Automated report-text check (proxy, not a study).** Offline script scoring template daily reports for reading level, length, named next steps, banned wording; write `reports/metrics/report_text_check.json`; label it clearly as a proxy in LIMITATIONS and RESEARCH_GAPS. Add to `check_docs_numbers.py` only if numbers are quoted in docs.
7. **Finish.** Full verification (all commands in section 3), update `docs/LIMITATIONS.md`, `docs/COMPONENTS.md` (`PYTHONPATH=. python scripts/make_components_doc.py`), commit, tag `echos-v3.2`.

## 7. Prompt to paste into a fresh session

```
Read CLAUDE.md and docs/HANDOFF.md fully before doing anything. Implement section 6 "NEXT PHASE" of docs/HANDOFF.md, items 1 to 7 in order, one commit per item.

Rules: keep output short; no foreground step over ~3 minutes (long runs in the background with a 10-minute cap, poll with Monitor); NO Gemini, NO PubMed, NO real email anywhere (ECHOS_REPORT_LLM=off, ECHOS_PUBMED=off, ECHOS_DRY_RUN=1; FakeLLM / FakeMailer only); never print or open secrets or .env; do not retrain models; do not run the rating study or any real-LLM check. Use .venv/bin/python and PYTHONPATH=. for scripts.

Start by telling me in at most 10 lines what you will do and any decision you need from me. Then implement. Write tests for every new behaviour, keep scripts/check_docs_numbers.py passing, and at the end run every Python test file in its own process, the three Playwright suites (replay, live, auth), npx tsc -b, npm run build, node contrast.mjs, then fix anything broken, commit and tag echos-v3.2.

Final report (short): what was built per item, test results per file and per Playwright suite, what is still not done or only partly verified (be honest), and the tag.
```
