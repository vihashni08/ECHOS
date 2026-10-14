# Data policy (Data Quality agent, `echos/agents/data_quality.py`)

Rule-based, no ML. Numbers come from `reports/eda/DATA_QUALITY.md` and `reports/metrics/data_quality.json`.
The Data Quality agent runs before the Health Monitoring and Fall agents: `echos/data/patient_day.py` builds the patient-day table from its cleaned output, and in the
pipeline the Health Monitoring agent waits for the Data Quality agent's output of the same event (unless it looks dead, see "Failure").

| Step | Rule |
|---|---|
| Schema / types | Required columns checked (`DataQualityError` otherwise); timestamps parsed (unparseable rows counted and dropped); values coerced to numbers (non-numeric kept as NaN and listed as invalid). |
| Duplicates / order | Exact duplicate rows dropped (counted); rows sorted by (patient, timestamp). Out-of-order rows are counted. Unknown Activity rooms dropped (counted). |
| Range checks | Per device_type limits in `config.yaml: data_quality.ranges` (Body Temperature 34-42, Skin Temperature 20-40, systolic 60-250, diastolic 30-150, heart rate 30-220, weight 30-200, body water 30-80). An implausible value becomes NaN and is listed in the day's `invalid_values`. It is never dropped silently and never imputed. Types without a range (muscle mass) are not checked. |
| Vitals missing | No imputation. A day without a BP/HR reading has NaN vitals, `has_reading = False` and `days_since_last_reading` (0 if read today, otherwise days since the last PAST reading; if never read, days since the patient's first record + 1). Baselines use only past readings (shift(1)); nothing is forward-filled. |
| Activity sensor gap | A day with zero events, or fewer than 5% of the patient's PAST median of daily counts (median over earlier non-gap days, at least 3 of them; zero is always a gap), is a SENSOR_GAP. It is not "zero activity": behaviour features are NaN, `has_activity = False`, `sensor_gap = True` with reason `zero_events` / `low_events` / `no_activity_record` (outside the patient's activity window). Gap days do not enter the median, so a gap cannot lower the bar. |
| Completeness | 0-1 = 0.5 x usable activity + 0.4 x has vitals + 0.1 x no invalid values. |
| Failure | The Data Quality agent never raises: on any error it returns a pass-through result with `quality_checked = False` and the reason `quality unchecked`. Triage treats that (and a missing Agent 0 output) as DATA_GAP. The Health Monitoring agent stops waiting for it if its heartbeat is stale. |

## Triage and reports
DATA_GAP (sensor gap, quality unchecked/missing, no vitals for more than `triage.max_vitals_gap_days` = 7 days) adds a
`DATA_GAP: ...` reason and the day can never be NORMAL (MONITOR at least; URGENT still wins). Daily reports carry a data note
built from the Data Quality agent's output (sensor gap, days without vitals, ignored implausible values, unchecked data).

## Output schema
`DataQualityResult` in `echos/schemas.py`: has_vitals, invalid_values, sensor_gap, sensor_gap_reason, days_since_last_reading,
completeness, quality_checked. The worker emits it inside the standard `AgentResult.evidence` (agent = `quality`).

## Retention of raw live events (optional)
Live / emulator databases keep every ingested raw event in `event_log`. `scripts/archive_events.py` (`echos/live/retention.py`) moves old raw events into a compressed JSON-lines file (`data/archive/event_log_*.jsonl.gz`, git-ignored) and deletes them from the database. It is a **dry run unless `--yes` is given** (`--dry-run` is also accepted); nothing is scheduled automatically.

```bash
PYTHONPATH=. .venv/bin/python scripts/archive_events.py --db data/echos_live_dev.db --older-than-days 30            # counts only
PYTHONPATH=. .venv/bin/python scripts/archive_events.py --db data/echos_live_dev.db --older-than-days 30 --yes      # archive + delete
```

- **Kept in the database:** `live_day` (the closed patient-day rows every detector and chart reads), `case_state`, `case_file`, `alerts`, `alert_state`, `reports`, `outbox`, `agent_output`, and by default all `vitals` events (vital charts and personal baselines read them; `--include-vitals` archives them too).
- **Never archived:** the open day of any patient; events after the `agent_checkpoint` `'live'` position; any event an alert or a detector output (a fall's waveform) points at; events of a day not yet closed in `live_day`; a day not more than N days older than **both** the wall clock and that patient's newest data day (so an emulator replaying 2019 keeps its latest days).
- **Order of work:** the archive file is written, read back and checksummed first; if the check fails nothing is deleted. The guards are checked again inside the DELETE.
- **Restart recovery:** `LiveProcessor.recover()` continues from the checkpoint (it only re-reads events *after* it) and rebuilds the open day from `event_log`; both only need rows the archiver never removes (tested: decisions and provisional flags are identical after archive + restart). Do **not** delete or reset `agent_checkpoint` after archiving: a processor starting from event 0 would rebuild closed days from incomplete raw data.
- **Side effects:** archived events are no longer deduplicated (a re-sent old event is stored again; its day is closed so it is only logged as late); in emulator mode the routine charts of archived days fall back to the original TIHM CSV rows, in real mode they show nothing for those days (the day-level numbers in `live_day` remain). There is no restore command: the archive is the record (`echos.live.retention.read_archive` reads it; re-importing is manual).
- **Not done:** no scheduled run, no encryption of the archive file (protect it like the database), no archive of the other tables. Tested with synthetic rows and one emulated run, not on a database of months of real data.
