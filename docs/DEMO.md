# 5-minute demo

Everything is offline: dry-run mail (nothing is sent), no Gemini, no live PubMed. Two modes; the badge in the header always says which one you are in.

## Start the live / emulator mode (the main demo)

One command: `scripts/start.sh --live --fresh` (then open the printed web URL). Or by hand:

Terminal 1, from the repo root (fresh database; the first start also builds the replay fall model):
```bash
rm -f data/echos_live_dev.db data/echos_live_dev.db-wal data/echos_live_dev.db-shm
scripts/run_api.sh --dry --no-llm --demo --mode live --db data/echos_live_dev.db
```
Terminal 2:
```bash
cd web && npm run dev
```
Open **http://localhost:5173** (use `localhost`, not `127.0.0.1`).

| Min | Show | Say |
|---|---|---|
| 0:00 | **Today** (empty) | "ECHOS watches the sensors and vitals of older people at home and tells carers what needs action. The badge says EMULATOR: a labelled device emulator replays real TIHM sensor events in real time; it is not a real device. Real devices would use the same ingestion API." |
| 0:30 | **Demo tools** -> *Start* | "Events arrive; each person gets a status as the data comes in. A day stays provisional until it closes." Point at *Is the data arriving?* and *What the agents are doing*. |
| 1:15 | **How it works** | "Three detectors sense; four agents act: Triage decides with plain rules, the Case Investigator builds a case file, the Care Coordinator owns the alert until someone acts, the Report agent words it. They talk over a message bus." Show a detector card (Never: ...) and the bus. |
| 2:00 | **Demo tools** -> *Inject a fall now* | "A SisFall recording is written as one accelerometer event at the patient's current data time. The URGENT alert is queued before any other work: fixed template, no LLM." Back on **Today**: the alert card, the escalation countdown. |
| 2:45 | **Open case** (People -> the person) | "How this decision was made": headline, evidence level (not a probability), suggested next checks, and the Investigator's step-by-step trace. |
| 3:30 | **Acknowledge** | "Acknowledging stops the escalation timer; the Care Coordinator opens a check-in task. If nobody acknowledges, the same alert goes to a second contact." (Alerts page: delivery log, tasks.) |
| 4:15 | **Today** -> *Data needs checking* (only shown when a person has gone more than 7 days without a vitals reading; the 4-patient demo data usually has none, then skip this line) | "A day with only data problems is a device to check, not a change in the person; it can never be Normal." |
| 4:40 | **System** -> limitations | "What we do not claim: clinical validity, forecasting, novelty. Small cohort, simulated falls, no real device." |

## One-week demo (critical alert + fall + daily and weekly reports)

```bash
scripts/start.sh --week-demo
```
Open http://localhost:5173, then **Demo tools -> Play**. One person (46286), Mon 2019-05-27 to Sun 2019-06-02, built fresh each start (`data/echos_week_demo.db`, dry-run mail with dummy addresses, no LLM): an injected fall on day 1, critical low blood pressure readings on 4 days, 7 daily reports and the weekly PDF (written when Sunday closes). Window and patient come from `ECHOS_DEMO_PATIENTS / _START / _FALLS` (echos/sim/replay.py).

## Replay mode (evaluation)

```bash
scripts/make_ui_dev_copy.sh
scripts/run_api.sh --dry --no-llm --demo --db data/echos_ui_dev.db
```
Same web app. *Demo tools* then holds the simulation controls and, with `--demo`, the agent kill switch; the **Demo guide** walks through reset and play, a fall, killing the Fall detector worker and the weekly report. `reports/metrics/kill_test.json` has the measured recovery (detect 2.8 s, recover 3.9 s).

## If something fails

| Problem | Do |
|---|---|
| The UI shows no data or "API not reachable" | Check terminal 1; open http://127.0.0.1:8000/docs. Use `localhost:5173`, not an IP. |
| "runtime not running" in the header | The runtime process died; restart `scripts/run_api.sh`. |
| Port in use | Stop the process that holds 8000 (API) or 5173 (UI). |
| Emulator never starts | Press *Start* in Demo tools; the first start loads TIHM raw files (a few seconds). |
| Fall is not URGENT | A flagged fall without post-fall inactivity is only MONITOR (by design). Inject again; the API picks a random held-out recording. |
| Vite will not start | `cd web && npm ci`; or show `web/screenshots/` (`node scripts/ui_screenshots.mjs`). |
