# ECHOS refactor plan (assessment + implementation plan)

Written after a full re-read of CLAUDE.md, docs/LIMITATIONS.md, RESEARCH_GAPS.md, ARCHITECTURE.md, the config, the orchestrator/sim/agents code and the current web UI. Nothing here has been implemented yet.

## 1. Honest assessment: what is actually wrong

### 1.1 The purpose is undefined
The repo says "decision support, not diagnosis" but never answers: **who uses it, what decision does it support, and when?** Today it is a research pipeline (models + metrics) with a demo UI bolted on. A reader cannot tell whether ECHOS is a caregiver alert app, a clinician dashboard or a thesis evaluation harness.

### 1.2 It is not a live monitoring system
- The only data source is the **Simulator**, which replays 3 months of TIHM CSVs plus injected SisFall windows. The "Play" button on the Dashboard *is* the data source.
- There is **no ingestion interface**: no way for a real sensor, a hub or a file drop to feed events in. Everything enters through `echos/sim/controller.py`.
- The orchestrator decides **once per patient-day** (replay fall delay: median 13.1 h, max 23.8 h). A live system must decide on rolling windows, minutes after the event. Only falls are windowed (2.5 s); vitals and behaviour models only know "a whole day".
- Timestamps are simulated time; "live" in the UI means "SSE from SQLite".
- Consequence: every claim about latency, outages and alerts is about a replay, and the UI cannot tell the viewer that.

### 1.3 The agents are not clearly defined
Seven things are called "agents" but they are different kinds of component:

| Today's name | What it really is |
|---|---|
| Data Quality agent | Deterministic rule-based validation stage |
| Health Monitoring agent | Two trained models (XGBoost) + SHAP; a detector service |
| Fall agent | Random forest + inactivity rule; a detector service |
| Orchestrator | A rule engine (if-then triage), wrapped in LangGraph |
| Report agent | The **only** genuinely agentic part (LLM + tool loop + validator) |
| Supervisor | Process manager |
| Simulator | Test fixture, shown as part of the product |

There is no contract saying what each owns, what it consumes and emits, who may call whom, or what each costs when it fails. The UI just shows three "alive 0 s ago" boxes with a "Kill agent" button.

### 1.4 The frontend does not explain the system
Looking at the current Dashboard:
- The first things you see are **simulation controls and kill buttons** (demo tooling), not "who needs attention".
- Patient cards show hex ids, "Latest day", "Last alert Fall alert 2019-05-13 10:18": no reason, no trend, no "what should I do", no data freshness.
- The alert feed is a flat list of falls. There is no priority, no state (new / seen / handled), no link between an alert and its explanation.
- Nothing says which parts are real, which are simulated, and which models are weak (e.g. agitation PR-AUC 0.22).
- The agent strip shows liveness but not what each agent *did*. There is no end-to-end view of "how this decision was made".
- Six pages for three audiences (caregiver, clinician, operator) with no role framing.

### 1.5 Known technical gaps (from LIMITATIONS.md and the code)
1. MONITOR share is **28.8%** of patient-days, above the project's own 25% usability limit. A fix (`triage.vitals_prob` 0.4, about 24.1%) is proposed but **not applied**.
2. Thresholds were chosen and scored on the same out-of-fold predictions: replay results are optimistic. There is no untouched final test set.
3. Vitals arrive about every other day (39.9% of days have no reading). Detection is inherently delayed; the UI does not show "last reading N days ago".
4. Falls: elderly evidence is **one subject** (SE06), sensitivity 0.56 for that subject. Overall 51/60 injected falls detected at the strict operating point.
5. Agitation score is modest and between-patient differences drive much of the pooled skill.
6. **No alert lifecycle**: no acknowledge, no escalation if nobody reacts. For a safety system this is the biggest functional hole.
7. No authentication, no roles; API and UI are demo-only.
8. Real SMTP and real Gemini output are never evaluated; the report rating kit was never run with readers.
9. The supervisor is a single point of failure for restarts.
10. Report text is only validated against the rules encoded; no human-rated quality.
11. Fault injection is small (3 patients x 10 days, fakes, one machine).
12. Training data mismatch: TIHM labels are daily; a live system needs intraday features that have never been trained or evaluated.

## 2. Target definition (the missing "why")

**Purpose statement (proposed):**
> ECHOS watches the home-sensor, vital-sign and movement data of one older person and tells their caregiver and clinician, in plain words, **(a) immediately when a fall or critical vital sign is detected, and (b) once a day whether anything differs from that person's own usual, with the evidence.** It never diagnoses and never decides an emergency with an LLM.

**Users and their one question:**
| User | Question | Surface |
|---|---|---|
| Caregiver | "Is X okay today? Do I need to do anything now?" | Today view + alert page |
| Clinician | "Who changed this week, and on what evidence?" | Weekly review + patient timeline |
| Operator / examiner | "Is the system healthy and is the data live or simulated?" | System page |

**Two honest modes, always labelled in the UI:**
- **Replay mode** (TIHM + SisFall): for evaluation and demos.
- **Live mode**: events arrive through a real ingestion endpoint with real timestamps (see Phase 1). Without a physical source this is demonstrated with a *device emulator* that streams in wall-clock time, clearly named as an emulator.

## 3. Target architecture (clear agent roles)

```
Sources (adapters)         Ingestion        Detectors (services)        Decision        Communication
 - Replay adapter     -->  /ingest API  --> Quality gate          -->  Triage rules --> Alerting (outbox, ack, escalation)
 - Device emulator         event_log        Vitals detector              (if-then)      Report agent (the only LLM agent)
 - (real hub, future)                       Behaviour detector                         Weekly report
                                            Fall detector (streaming)
                    Supervisor + health  --  observes everything, restarts workers
```

Rules for the refactor:
- **Rename for honesty:** *Detectors* (vitals, behaviour, fall) and *Quality gate* are services; the **Report agent** is the only "agent". The orchestrator is the *Triage engine*. Keep the code, change names in docs/UI/API (`/api/agents` becomes `/api/components`, with a `kind` field).
- Each component gets a one-page contract in `docs/COMPONENTS.md`: inputs, outputs (AgentResult schema), failure behaviour, latency target, what it must never do.
- Sources are pluggable adapters implementing one `EventSource` interface; the simulator becomes one adapter, no longer the product's centre.

## 4. Implementation plan (phases, each ends in a commit + tests)

### Phase 0: Define (0.5 day, docs only)
- Add the purpose statement, users, modes and component table to README, ARCHITECTURE.md; new `docs/COMPONENTS.md`.
- Retire "agent" wording for non-agentic parts everywhere (docs, API labels, UI).
- Acceptance: a new reader can state in one sentence what ECHOS does and who uses it; `check_docs_numbers.py` passes.

### Phase 1: Real ingestion and a live mode (2-3 days)
- `POST /api/ingest/events` (batch of activity, vitals, accelerometer-window events with device id and timestamp) validated by pydantic, written to `event_log`; idempotent by (device, ts, type).
- `EventSource` interface; refactor `sim/replay.py` into `ReplayAdapter`; add `EmulatorAdapter` that streams in **wall-clock time** with jitter, gaps, duplicates and late arrivals (so the quality gate is exercised for real).
- Replace "decide once per patient-day" with **rolling decisions**: re-run triage on each new event batch (debounced), with the day status becoming "as of HH:MM". Fall path: streaming windows from the buffer, alert queued per window, unchanged template path.
- Show data **freshness** (last event per stream) and mark a silent sensor as DATA_GAP after N minutes, not at day end.
- Acceptance: in live mode an emulator-injected fall produces an alert in under 5 s wall-clock; in replay mode the UI banner says REPLAY; a unit test proves the same triage code serves both modes.
- Open limit to state, not hide: vitals/behaviour models are trained on daily features; intraday scoring will use "as-of-now" partial-day features and must be evaluated (Phase 3) before any accuracy claim.

### Phase 2: Alert lifecycle (1-2 days)
- States: `NEW -> SENT -> ACKNOWLEDGED -> RESOLVED` plus `ESCALATED` when unacknowledged after a configured time (fall: 5 min, critical vitals: 30 min) to a second contact (`ECHOS_ESCALATION_EMAIL`).
- Ack by signed link in the email and by button in the UI; every transition timestamped in the DB.
- Acceptance: tests with a fake clock for escalation; no duplicate escalation after restart.

### Phase 3: Fix known model/triage gaps (2-3 days, offline)
- Apply the proposed `vitals_prob` 0.4 (MONITOR about 24%) **only** after evaluating on a patient-level held-out split not used for threshold selection (nested: choose on folds 1-4, report on fold 5, repeat); write to `reports/metrics`.
- Evaluate intraday (as-of-hour) features for the behaviour model; report honestly if it is worse.
- Re-run ablation and fault injection on the new rolling decision path; regenerate RESEARCH_GAPS.
- Acceptance: LIMITATIONS.md numbers regenerated from metrics JSON; MONITOR share below 25% on held-out data or documented as not met.

### Phase 4: Frontend redesign (3-4 days)
Information architecture, answer-first:
1. **Today (home)**: a ranked "Needs attention" list at the top (Urgent, then Changed), each row = person, one-sentence reason in plain words, how long ago, data freshness, and one primary action (Acknowledge / Open). Below: "All people" as compact rows with status, last reading ages, 7-day mini-trend. Normal people are collapsed.
2. **Person page**: timeline of the last 14 days (status per day), vitals vs personal band, routine vs usual, each flag with its "why it fired", and a "How this decision was made" panel showing the pipeline steps (quality gate -> detectors -> triage rule that fired -> report) with real values.
3. **Alerts**: inbox with lifecycle state, ack/resolve, delivery status (dry-run vs real).
4. **Clinician week**: weekly summary table across all people, drill into evidence and sources.
5. **System / About**: what each component does (plain text from COMPONENTS.md), health, data mode (REPLAY / LIVE / EMULATOR), and a "Known limitations" card with the key numbers.
6. **Demo tools** (play, speed, inject fall, kill agent) move into a collapsible drawer visible only in demo mode, never on the home page.
- Global mode banner (REPLAY / EMULATOR / LIVE) in the header; every timestamp labelled simulated or real.
- Plain-language rule: no hex ids in headings (display name or "Patient 1"), no jargon (SHAP, CUSUM) outside an "evidence" expander.
- Acceptance: a first-time viewer can say within 10 s who needs attention and why; Playwright tests for the new flows; axe checks stay clean; WCAG contrast script passes.

### Phase 5: Safety and operations (1-2 days)
- Simple authentication (single shared token, then roles caregiver/clinician/operator); the API stays localhost unless configured.
- Supervisor watchdog: a tiny second process that restarts the supervisor (removes the stated single point of failure) or documents a systemd/launchd unit.
- Real SMTP test in a controlled run with `scripts/send_test_email.py --yes` (user-run only).

### Phase 6: Evaluation of what is still unmeasured (1-2 days + people)
- Run the rating kit with 3-5 readers on template and (separately, user-approved) real-LLM reports.
- Run `live_report_check.py --yes` once with the new structured-output schema (user-approved, capped).
- Expand fault injection to all patients and 10 days where runtime allows.

## 5. Suggested order and effort
Phase 0 -> 1 -> 4 (UI built on the new live/replay model) -> 2 -> 3 -> 5 -> 6. About 12-16 working days in total. If time is short before your review, do **Phase 0, the Today page and Person page of Phase 4, and the mode banner** first: they fix the "nothing is clear" problem without needing new infrastructure.

## 6. Decisions needed from you
1. **Live source:** is an emulator streaming in wall-clock time acceptable as the "live" demonstration (honest labelling), or do you have access to a real device/hub or data feed?
2. **Scope for the next milestone:** full plan, or the quick-win subset (Phase 0 + Today/Person pages + mode banner)?
3. **Audience priority:** caregiver-first, or clinician-first, for the home page?
4. **Naming:** OK to rename "agents" to detectors/services and keep "agent" only for the Report agent?
