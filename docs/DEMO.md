# 5-minute demo

Everything is simulated and offline: dry-run mail (nothing is sent), no Gemini, no live PubMed. Needs `data/echos.db` (built by `scripts/run_demo.py 4 28`, see the README) and a Python 3.11 venv in `.venv`.

## Start (before the audience arrives, ~1 minute)

Terminal 1, from the repo root (a copy of the database, so the demo never changes `data/echos.db`):
```bash
scripts/make_ui_dev_copy.sh
scripts/run_api.sh --dry --no-llm --demo --db data/echos_ui_dev.db
```
Terminal 2:
```bash
cd web && npm run dev
```
Open **http://localhost:5173** (use `localhost`, not `127.0.0.1`: the API allows only that origin). API docs: http://127.0.0.1:8000/docs. The top bar shows "Demo" and a **Demo guide** button.

## Script

| Min | Show | Say |
|---|---|---|
| 0:00 | **Dashboard** (`/`) | "ECHOS is decision support for people living at home, not a diagnosis. Each card is a patient-day: Normal, Changed-monitor or Urgent, decided by plain if-then rules. The data are TIHM replays; the falls are simulated." Point at the agent strip (three workers + orchestrator) and the alert feed. |
| 0:45 | **Demo guide** -> step 1 *Reset and play* | "A simulator replays the study day by day." Watch cards fill. |
| 1:15 | **Caregiver** (guide step 2, *Open the latest day*) | "A daily report in plain words: what changed against this person's own usual, what to do, a data note. Open 'How this report was made'." Show model / template, tools used, sources, validator result. "NORMAL days are a template with no LLM; only Monitor and Urgent days retrieve guidance." |
| 2:00 | **Patient** page (`/patients/<id>`) | "Vitals against the person's own band and routine against their own usual, not fixed thresholds. Readings come about every other day, so gaps are shown, never filled." |
| 2:30 | **Fall Monitor** -> guide step 3 *Inject a fall* | "A SisFall accelerometer window is injected. The page says it is a simulated signal. An URGENT alert is queued before any report work, to both caregiver and clinician, from a fixed template: no LLM." Show the waveform and the *Alert sent* panel (dry run). |
| 3:15 | **Dashboard** or **System** -> guide step 4 *Kill the Fall agent* | "The supervisor notices the dead worker and restarts it. A fall injected meanwhile is delayed by the restart time, not lost. The supervisor itself is a single point of failure for restarts." Show the heartbeat age growing, then alive. |
| 4:00 | **Clinician** -> guide step 5 *Send the weekly report* | "The weekly PDF: vitals against the personal band with a 'why it fired' line, findings mapped to guideline sources, considerations that name a concrete value. Delivery goes through a retrying outbox." Show the new outbox row, status SENT, channel *Dry run*. |
| 4:30 | **System** -> limitations | "What we do not claim: clinical validity, forecasting, novelty. Small cohort, simulated falls, one elderly subject. See `docs/LIMITATIONS.md`." |

## If something fails

| Problem | Do |
|---|---|
| The UI shows no data or "API unreachable" | Check terminal 1 is running; open http://127.0.0.1:8000/docs. Restart `scripts/run_api.sh ...`. Make sure you opened `localhost:5173`, not an IP. |
| Port in use | `ECHOS_API_PORT=8001 scripts/run_api.sh ...` and start Vite with a matching proxy, or stop the old process. |
| Simulation does not play / inject fails | Use *Reset and play* again; or run the API read-only: `scripts/run_api.sh --dry --no-llm --no-runtime --db data/echos_ui_dev.db` and walk through stored days, reports and the stored fall alerts on the Fall Monitor. |
| Kill step greyed out | The API was not started with `--demo`; restart it with `--demo`. Otherwise describe the recovery using `reports/metrics/kill_test.json` (detect 2.8 s, recover 3.9 s) and the fault-injection table in `reports/RESULTS.md`. |
| Vite will not start | `cd web && npm ci`; or show the prebuilt screenshots (`node scripts/ui_screenshots.mjs` writes `web/screenshots/`) and the API docs page. |
| Database missing / corrupt | `scripts/make_ui_dev_copy.sh` again; if `data/echos.db` is missing, `PYTHONPATH=. python scripts/run_demo.py 4 28` (~3 min). |
| Last resort | Open a stored weekly PDF from `data/reports/weekly/` and `reports/RESULTS.md`, and talk through `docs/ARCHITECTURE.md`. |
