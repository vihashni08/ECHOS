# Data policy (Agent 0, `echos/agents/data_quality.py`)

Rule-based, no ML. Numbers come from `reports/eda/DATA_QUALITY.md` and `reports/metrics/data_quality.json`.
Agent 0 runs before Agents 1-3: `echos/data/patient_day.py` builds the patient-day table from its cleaned output, and in the
pipeline Agents 1-2 wait for Agent 0's output of the same event (unless Agent 0 looks dead, see "Failure").

| Step | Rule |
|---|---|
| Schema / types | Required columns checked (`DataQualityError` otherwise); timestamps parsed (unparseable rows counted and dropped); values coerced to numbers (non-numeric kept as NaN and listed as invalid). |
| Duplicates / order | Exact duplicate rows dropped (counted); rows sorted by (patient, timestamp). Out-of-order rows are counted. Unknown Activity rooms dropped (counted). |
| Range checks | Per device_type limits in `config.yaml: data_quality.ranges` (Body Temperature 34-42, Skin Temperature 20-40, systolic 60-250, diastolic 30-150, heart rate 30-220, weight 30-200, body water 30-80). An implausible value becomes NaN and is listed in the day's `invalid_values`. It is never dropped silently and never imputed. Types without a range (muscle mass) are not checked. |
| Vitals missing | No imputation. A day without a BP/HR reading has NaN vitals, `has_reading = False` and `days_since_last_reading` (0 if read today, otherwise days since the last PAST reading; if never read, days since the patient's first record + 1). Baselines use only past readings (shift(1)); nothing is forward-filled. |
| Activity sensor gap | A day with zero events, or fewer than 5% of the patient's PAST median of daily counts (median over earlier non-gap days, at least 3 of them; zero is always a gap), is a SENSOR_GAP. It is not "zero activity": behaviour features are NaN, `has_activity = False`, `sensor_gap = True` with reason `zero_events` / `low_events` / `no_activity_record` (outside the patient's activity window). Gap days do not enter the median, so a gap cannot lower the bar. |
| Completeness | 0-1 = 0.5 x usable activity + 0.4 x has vitals + 0.1 x no invalid values. |
| Failure | Agent 0 never raises: on any error it returns a pass-through result with `quality_checked = False` and the reason `quality unchecked`. Triage treats that (and a missing Agent 0 output) as DATA_GAP. Agents 1-2 stop waiting for Agent 0 if its heartbeat is stale. |

## Triage and reports
DATA_GAP (sensor gap, quality unchecked/missing, no vitals for more than `triage.max_vitals_gap_days` = 7 days) adds a
`DATA_GAP: ...` reason and the day can never be NORMAL (MONITOR at least; URGENT still wins). Daily reports carry a data note
built from Agent 0's output (sensor gap, days without vitals, ignored implausible values, unchecked data).

## Output schema
`DataQualityResult` in `echos/schemas.py`: has_vitals, invalid_values, sensor_gap, sensor_gap_reason, days_since_last_reading,
completeness, quality_checked. The worker emits it inside the standard `AgentResult.evidence` (agent = `quality`).
