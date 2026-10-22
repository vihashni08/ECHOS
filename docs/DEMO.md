# Demo script (about 10 minutes)

## Before the demo
```bash
scripts/start.sh
```

Open http://localhost:5173 and check that the top bar says **REPLAY** and **Live**. Mail is a dry run: nothing leaves the machine.

## 1. The problem (1 min), on How it works
- A small clinical team cannot read every reading from every home. The question each morning is who to look at, and why.
- **Label audit:** 88% of days with BP ≥ 160/100 are labelled, so "detecting" the labels from the same day's readings would be circular. That is why ECHOS ranks people for review and only alerts on red flags.

## 2. Start the agents (1 min)
1. Open **Demo tools** and choose 20 events/s.
2. Press **Play**.
3. Close the sheet.
4. Open **Agents**: three processes, alive, with heartbeats.
5. The **message trace** fills up. Point out:
   - `review request` (Patient Monitor → Review Coordinator)
   - `feedback` (Care agent → Patient Monitor)
   - `assign review` (Review Coordinator → Care agent)

## 3. Today (2 min)
- **Review list.** Four people are ranked by how far a risk is above its base rate. Read one card's reasons aloud: "agitation risk 24% in the afternoon window: fridge door 62 (usual-adjusted z +4.4)…" or "17 confirmed BP/pulse event days in 26 days: personal rate 60% (95% CI 42–77%)".
- **Fairness.** A patient unseen for a week is picked even with a low score.
- **Urgent alerts.** Point out the escalation countdown.

## 4. One person (2 min)
Open **Patients** and choose 46286.
- **BP and pulse** against their own usual. The dashed line starts at the population average and moves to their own level (cold start), and red-flag readings are marked.
- **The 3-day risk climbs** as confirmed events arrive. This is the Patient Monitor learning from the feedback.
- **The day log and their agent messages.**

## 5. Red flag → URGENT (1 min)
1. Open **Demo tools** → **Inject a reading**.
2. Choose a patient and "Severe high BP 192/124", then press **Inject**.
3. Open **Alerts**: the new red-flag alert, queued about 0.2 s after the reading.
4. Press **I have seen this**: the escalation timer stops.

## 6. Crash an agent (1 min)
1. On **Agents**, kill the Patient Monitor.
2. About 12 s later: "alive again", restarts: 1.
3. Nothing was lost. The kill test (`reports/metrics/v2_runtime.json`) shows identical review picks and alerts with and without the crash.

## 7. Results and limits (2 min), on How it works
- **Agitation:** PR-AUC 0.27 vs 0.016 chance; 88 of 135 episodes caught.
- **BP / pulse priority:** 0.62 vs 0.28; better than the study's own threshold rule.
- **Coordinator:** 0.45 vs 0.25 events per review.
- **Limits:** 41–56 patients over 3 months; labels are confirmed alerts; no forecasting of new episodes; replay, not live patients.

## If something goes wrong
| Problem | Fix |
|---|---|
| "API not reachable" banner | Restart `scripts/start.sh` |
| Play says the store holds events | Use **Reset** in Demo tools (or restart; the demo database starts fresh) |
| Charts are empty | The replay has not reached that person yet |
