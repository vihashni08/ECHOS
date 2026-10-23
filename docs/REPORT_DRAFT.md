# ECHOS: Integrated Healthcare Monitoring and Decision Support Using Cooperating Agents

*Draft report sections: Abstract, Introduction, Data, Method, Evaluation, Results, Discussion, Limitations, Conclusion.*
*Every number comes from `reports/metrics/v2_*.json` and can be regenerated with the scripts named in the text.*
*Citations marked [verify] must be checked against the original source before submission.*

---

## Abstract

Remote monitoring of people living with dementia produces many signals: daily vital signs, room and door sensor events, and alerts. A small clinical team can only review a few of them each day.

We present ECHOS, a decision-support system built from three cooperating software agents. Each has its own goal, memory and decisions:
- A **Patient Monitor** for each person learns that person's usual vital signs and daily activity.
- A **Review Coordinator** shares a fixed daily review budget across the caseload.
- A **Care agent** delivers urgent alerts and passes clinicians' outcomes back to the monitors, which learn from them.

The agents rely on four methods:
- personal Bayesian baselines (Normal-Inverse-Gamma)
- an empirical-Bayes Beta–Bernoulli event rate that shrinks new patients towards the population
- an activity-window agitation detector with time-of-day personal baselines
- a lift-based review allocation with a fairness floor

We evaluate on the public TIHM dataset (56 people with dementia, 3 months) using 5-fold cross-validation split by patient and patient-level bootstrap confidence intervals.

An audit of the labels shows that TIHM's blood-pressure labels largely follow a fixed threshold on the same day's reading. Same-day "detection" therefore re-learns the threshold, and new episodes cannot be predicted from daily vital signs. We design around this finding.

**Results:**

| Component | Result | Chance level |
|---|---|---|
| Agitation detection | PR-AUC 0.27 (95% CI 0.20–0.35), ROC-AUC 0.96; 88 of 135 confirmed episodes at one alert per patient-week | 0.016 |
| Vitals priority (personal Bayesian rate) | PR-AUC 0.62, significantly better than the study's own threshold rule (+0.050, CI 0.016–0.077) | 0.28 |
| Review Coordinator (4 reviews/day) | 0.45 confirmed events per review | 0.25 for random selection |

With real processes, urgent alerts are queued 0.29 s (median) after a red-flag reading. Killing an agent mid-run loses no alerts and leaves the review decisions unchanged.

---

## 1. Introduction

Home-monitoring programmes for older adults collect far more data than clinicians can read. The question that matters each morning is not "is this number abnormal?", which a threshold answers, but **"who should we look at today, and why?"**

An earlier version of this project trained classifiers to reproduce the dataset's daily labels and called its components "agents". It had two problems:
1. The models mostly re-learned how the labels were made.
2. The "agents" were pipeline stages without goals, memory or decisions of their own.

This version addresses both.

**Contributions:**
1. **A label-validity audit of TIHM** showing:
   - 88% of days with BP ≥ 160/100 carry a BP label.
   - 92% of BP labels lie within one hour of a reading.
   - New vitals episodes are close to unpredictable from daily vitals (PR-AUC 0.12–0.15 vs 0.10 chance).

   This shapes what can honestly be claimed from the dataset.
2. **Personal Bayesian monitoring methods** that suit small data:
   - a Normal-Inverse-Gamma baseline of each person's usual readings
   - an empirical-Bayes event rate that is most useful for new patients
   - an agitation detector whose personal time-of-day baseline adds a statistically significant gain
3. **A review-allocation rule** that puts two detectors on one scale (lift over each detector's base rate), with a fairness floor. It is chosen by a replay study against six alternatives.
4. **A three-agent runtime** in which each agent has a goal, a persistent belief, its own decisions, message-based communication and learning from feedback. It is tested with real processes, including a crash of one agent.

ECHOS is decision support. It does not diagnose, and no model decides an emergency: urgent alerts come from fixed clinical red-flag rules.

---

## 2. Related work

- **TIHM** (Palermo et al., *Scientific Data*, 2023) describes the dataset. Alerts were raised by rules and an analytical model, then validated by a clinical monitoring team.
- **Agitation prediction on TIHM** has recently been benchmarked using 6-hour windows (arXiv:2506.06306, 2025 [verify authors and title]). ECHOS differs in three ways: it adds personal time-of-day baselines, evaluates strictly by patient, and places detection inside a decision process.
- **Early-warning scores.** NEWS2 (Royal College of Physicians, 2017) defines single-parameter "red" scores that we reuse as the urgent tier. NICE NG136 (2019) defines severe hypertension as ≥ 180/120 mmHg.
- **Allocating scarce clinician attention** has been framed as a restless or collapsing bandit problem (e.g. Mate et al., NeurIPS 2020 [verify]). We tested a belief-based index policy in a pilot and it did not beat simpler policies on TIHM's labels (Section 7). We therefore use a transparent lift rule and report that negative result.
- **Agents.** We follow the classic definition of an agent as autonomous, reactive, proactive and social (Wooldridge & Jennings, 1995 [verify]). The design test in Section 4.5 makes this concrete.
- **Methods:**
  - Conjugate Bayesian models (Gelman et al., *Bayesian Data Analysis* [verify edition])
  - Hidden Markov models (Rabiner, 1989)
  - XGBoost (Chen & Guestrin, 2016)
  - Platt scaling (Platt, 1999)
  - PR-AUC for rare events (Saito & Rehmsmeier, 2015)

---

## 3. Data

TIHM (Palermo et al., 2023) contains 56 people living with dementia monitored at home between April and June 2019.

**Data used** (inclusion rules fixed before any results were seen):

| Source | Used for | Size |
|---|---|---|
| Systolic BP, diastolic BP, heart rate | Vitals monitoring | 41 patients with ≥ 14 days of BP readings; 2,201 calendar days, 1,655 with a reading |
| Room and door sensor events (8 locations) | Agitation detection | 1,030,559 events → 8,516 windows, 56 patients |
| Temperature, body water | Alert rules only | Same patients |
| Labels: blood pressure, pulse | Vitals target | 280 event days in the cohort |
| Labels: agitation | Agitation target | 135 labels, 27 patients |

**Excluded, with reasons:**
- **Sleep mat:** only 17 patients.
- **Skin temperature:** 13 patients.
- **Weight, temperature and body-water labels:** 4, 1 and 68 labels, and only 13 of the 68 body-water labels have a same-day reading.
- **Falls:** TIHM contains no falls, so fall detection was removed from the project.

**What a label is.** A label is an alert, raised by the study's rules or model, that the monitoring team confirmed as a true health event. Labels are confirmed events, not diagnoses.

---

## 4. Method

### 4.1 Label audit (`scripts/evaluation/label_audit.py`)

| Question | Finding |
|---|---|
| Do BP labels follow the same day's reading? | 88% of days with sys ≥ 160 or dia ≥ 100 are labelled, and these days hold 77% of BP labels. Median reading on labelled days: 163/91; on other days: 136/74 |
| Are labels tied to readings in time? | 92% of BP labels have a reading within 1 hour |
| When are agitation labels stamped? | Only at 08:00, 12:00 and 18:00 (plus one at 09:00): the study model's batch times |
| Can new vitals episodes be forecast? | 41 onsets (no label or crossing in the previous 7 days); 1–3-day-ahead PR-AUC 0.12–0.15 vs 0.10 chance |

**Design consequences:**
- Threshold crossings stay a **rule**.
- The vitals model ranks **who** tends to have confirmed events. It does not claim to forecast new episodes.
- Agitation is scored on the study's own windows.

### 4.2 Personal baseline (Normal-Inverse-Gamma)
Each vital of patient $i$ is modelled as $x \sim N(\mu_i, \sigma_i^2)$ with prior $(\mu_i, \sigma_i^2) \sim \text{NIG}(\mu_0, \kappa_0, \alpha_0, \beta_0)$.

The prior is fitted on training patients by moment matching:
- $\mu_0$ = mean of the patient means
- $\beta_0/(\alpha_0-1)$ = within-patient variance
- $\kappa_0$ = within-patient variance / between-patient variance

After the last $n \le 28$ readings with mean $\bar x$:

$$\kappa_n=\kappa_0+n,\ \ \mu_n=\frac{\kappa_0\mu_0+n\bar x}{\kappa_n},\ \ \alpha_n=\alpha_0+\frac n2,\ \ \beta_n=\beta_0+\frac12\sum_j(x_j-\bar x)^2+\frac{\kappa_0 n(\bar x-\mu_0)^2}{2\kappa_n}$$

The next reading is Student-$t$ with $2\alpha_n$ degrees of freedom, location $\mu_n$ and scale $\sqrt{\beta_n(\kappa_n+1)/(\alpha_n\kappa_n)}$.

ECHOS reports each reading against the person's own usual, e.g. "systolic 172 vs usual 138 (+3.1 SD)". A new patient starts at the population usual.

### 4.3 Personal Bayesian event rate (vitals review priority)
Each patient's daily rate of confirmed BP or pulse events is $r_i \sim \text{Beta}(a_0, b_0)$. The prior is estimated from the training patients by the beta-binomial method of moments:

$$\mu=\frac{\sum k_j}{\sum n_j},\quad v=\text{Var}\Big(\frac{k_j}{n_j}\Big)-\overline{\frac{\mu(1-\mu)}{n_j}},\quad s=\frac{\mu(1-\mu)}{v}-1,\quad a_0=\mu s,\ b_0=(1-\mu)s$$

- **Online estimate:** $\hat r_t=(a_0+k_t)/(a_0+b_0+n_t)$.
- **Three-day risk:** $1-(1-\hat r_t)^3$.
- **95% credible interval:** from $\text{Beta}(a_0+k_t,\ b_0+n_t-k_t)$.

The fitted prior strength was 2–5 pseudo-days, so a new patient moves to their own rate within days.

### 4.4 Agitation detector
- **Windows:** night 18:00–08:00, morning 08:00–12:00, afternoon 12:00–18:00.
- **Features:**
  - counts per location, total, events per hour
  - room-to-room transitions and door openings
  - longest quiet gap, rapid movements (< 60 s apart), active hours
  - room entropy
- **Personal baseline**, per feature and slot, from the previous 14 same-slot windows (≥ 4, past only): $z=(x-\text{median})/(\text{IQR}+1)$.
- **History, strictly before the window:** episodes in the last 21 days, and windows since the last episode.
- **Classifier:** gradient-boosted trees (XGBoost: 300 trees, depth 3).
- **Calibration:** Platt scaling fitted on out-of-fold scores within the training patients only.

### 4.5 The agents
**Design test.** A component is an agent only if it has its own goal, a persistent belief, its own decision, and communicates with others. Data checks, models, alert rules and the report template fail this test, so they are tools the agents call.

| | Patient Monitor (one per person) | Review Coordinator | Care agent |
|---|---|---|---|
| **Goal** | Know what is usual for this person; say how much they need a clinician today | Use K daily reviews where they catch most, without leaving anyone unseen | Every urgent case and assigned review reaches a person; their outcome flows back |
| **Belief** | NIG history per vital; Beta event counts; same-slot activity history; episode history | Last review day of each patient | Alert lifecycle state |
| **Decisions** | Red flag → URGENT now; after feedback, its risks and reasons | Who gets today's K slots | Send, resend or escalate; open review tasks |
| **Learns from** | Clinician outcome (feedback) | Review history | Acknowledgement times |

**Why exactly three.**
- Risk is per person, while the review budget is shared by everyone. One agent per person cannot see the others, so a separate agent must make the shared decision.
- Delivery works on a minutes-scale clock and has a different goal from risk estimation. Merging it with either would mix two jobs.

**Allocation rule.** Priority is the lift over each detector's own base rate:

$$r_i=\max\Big(\frac{v_i}{\bar v},\frac{a_i}{\bar a}\Big)$$

with $\bar v = 0.30$ and $\bar a = 0.036$ from out-of-fold scores. Patients unseen for ≥ 7 days may take up to half the slots. The plain probability union $1-(1-v)(1-a)$ was tested first; it let the larger vitals probabilities crowd out agitation.

**Alert tiers:**
- **URGENT** (immediate email): NEWS2 red scores (systolic ≤ 90; pulse ≤ 40 or ≥ 131; temperature ≤ 35.0 or ≥ 39.1) and BP ≥ 180/120. These fire on 3.5% of reading days (0.18 per patient-week), and the study confirmed an event on 86% of those days.
- **Attention** flags: TIHM's own daily-check limits, which would fire on 11.5% of days (60% confirmed). They appear in review reasons only.

**Message flow for one day:**
1. The Monitor scores the day's readings and windows. A red flag goes straight to Care as `urgent`, and Care queues the alert.
2. That day's outcome goes Care → Monitor as `feedback`. In replay this is the TIHM record; in live use, the clinician.
3. The Monitor updates its belief and sends the Coordinator a `review_request`.
4. At day close the Coordinator allocates the K slots, sends `assign` to Care, and sends a `review_decision` back to the Monitors.
5. Care opens the review tasks.

**Runtime.** The agents are separate operating-system processes under a supervisor that restarts any process whose heartbeat stops. They share no memory: the only channel is a SQLite message table. All work is idempotent, and every belief is persisted, so a restarted agent resumes exactly. Each patient is scored by the models of the cross-validation fold that excluded them, so the replay demonstration is not in-sample.

---

## 5. Evaluation protocol
- **Splits:** 5-fold cross-validation **by patient**. Priors, models, calibration and thresholds are fitted on training patients only, and every score is out-of-fold.
- **Alert budget:** one alert per patient-week.
- **Metrics:**
  - PR-AUC (the primary metric, because events are rare) and ROC-AUC
  - recall and precision at the budget
  - false alerts per patient-week
  - Brier score and calibration deciles
- **Uncertainty:** 95% confidence intervals from 1,000 patient-level bootstrap resamples. Differences use a paired bootstrap and are called significant only when the whole interval is above zero.
- **Allocation study:** a 92-day replay of all 56 patients (about 40 active per day) using the out-of-fold scores.
  - An agitation episode counts as *followed up* if the patient is reviewed that day.
  - A vitals event counts as *anticipated* if the patient was reviewed 1–3 days before it.
- **Runtime test:** real processes on the 8-patient, 28-day demo cohort, with and without a SIGKILL of the Patient Monitor half-way through.

---

## 6. Results

### 6.1 Agitation detection (56 patients, 8,516 windows, 135 episodes, prevalence 0.016)

| Method | PR-AUC [95% CI] | ROC-AUC | Recall @1/pw | Precision | Caught |
|---|---|---|---|---|---|
| Time slot only | 0.022 [0.01–0.04] | 0.62 | 0.03 | 0.01 | 4 |
| Episode history only | 0.067 [0.03–0.12] | 0.79 | 0.27 | 0.09 | 37 |
| Activity counts, no personal baseline | 0.227 [0.18–0.28] | 0.94 | 0.56 | 0.18 | 75 |
| Logistic regression, same features | 0.283 [0.19–0.38] | 0.95 | 0.59 | 0.19 | 79 |
| **ECHOS detector** | **0.270 [0.20–0.35]** | **0.96** | **0.65** | **0.22** | **88** |

- **The personal time-of-day baseline adds a significant gain:** +0.042 PR-AUC over raw counts (CI 0.005–0.088).
- **It is not just learning the clock:** time of day alone is near chance.
- **Calibrated:** Brier 0.013. In the top decile, mean predicted probability 13% vs 14% observed.
- **Robust:** without the patient with the most labels (33), PR-AUC is 0.25.
- **By window:** night 0.25 (11 episodes), morning 0.25, afternoon 0.35.
- **Logistic regression is statistically tied** (−0.013, CI −0.073 to 0.051). XGBoost is kept for its calibration (Brier 0.013 vs 0.073).

### 6.2 Vitals review priority (41 patients, 2,078 days, prevalence 0.28)

| Method | PR-AUC [95% CI] | ROC-AUC | Precision @1/pw |
|---|---|---|---|
| TIHM threshold crossed today | 0.569 [0.38–0.69] | 0.74 | 0.67 |
| Persistence (label today) | 0.588 [0.40–0.70] | 0.74 | 0.65 |
| XGBoost (personal features + history) | 0.597 [0.42–0.70] | 0.74 | 0.74 |
| Personal label rate, no pooling | 0.611 [0.42–0.72] | 0.74 | 0.73 |
| HMM (EM) + readings + personal onset | 0.531 [0.39–0.62] | 0.71 | 0.65 |
| Bayesian rate + today's readings | 0.616 [0.44–0.72] | 0.77 | 0.74 |
| **ECHOS personal Bayesian rate** | **0.619 [0.43–0.72]** | **0.77** | **0.72** |

- **Significantly better** than the TIHM threshold (+0.050, CI 0.016–0.077) and than every HMM variant.
- **Statistically tied** with XGBoost and the un-pooled rate.
- **Best for new patients:**

  | Window | ECHOS rate | Un-pooled rate |
  |---|---|---|
  | First 7 days | 0.57 | 0.52 |
  | First 21 days | 0.66 | 0.63 |

- **Today's readings add nothing** beyond the rate, consistent with the audit.
- **Slightly over-confident at the top:** in the top decile, mean forecast 0.87 vs 0.77 observed.

### 6.3 Review allocation (56 patients, 92 days)

| K = 4/day | Agitation followed up | Vitals anticipated | Events per review | Never reviewed |
|---|---|---|---|---|
| Random | 0.11 | 0.27 | 0.25 | 5 |
| Round robin | 0.12 | 0.28 | 0.26 | 7 |
| Vitals only | 0.04 | 0.49 | 0.39 | 29 |
| Agitation only | 0.64 | 0.16 | 0.37 | 23 |
| Probability union | 0.11 | 0.50 | 0.43 | 28 |
| **ECHOS (lift)** | **0.61** | 0.29 | **0.45** | 22 |
| ECHOS + fairness | 0.51 | 0.30 | 0.42 | **8** |

- At K = 4, ECHOS yields 1.8× the confirmed events per review of random selection and covers both conditions.
- **The fairness floor** cuts patients never reviewed from 22 to 8 for about 6% fewer events per review.
- **At other budgets:**

  | Budget | ECHOS | Best single-detector policy | Random |
  |---|---|---|---|
  | K = 2 | 0.46 (0.50 with fairness) | 0.45 | 0.27 |
  | K = 6 | 0.42 | 0.35 | 0.26 |

### 6.4 Runtime (real processes, 8 patients × 28 days, 470 events)

| Check | Result |
|---|---|
| Patient-days scored | 222 |
| Days allocated | 28 (112 reviews) |
| Messages left unconsumed | 0 |
| URGENT latency, reading → alert queued | median 0.29 s, max 0.43 s |
| URGENT latency, reading → dry-run email sent | median 0.29 s |
| Patient Monitor killed (SIGKILL) half-way | detected and restarted after 12 s |
| Kill run vs uninterrupted run | 0 lost alerts, 0 extra alerts, identical review picks and statuses |

---

## 7. Discussion

**What the data supports.** The strongest signal in TIHM is in the activity sensors: personal, time-of-day-aware features recover two-thirds of confirmed agitation episodes at one alert per patient-week. The daily vital signs mostly tell us *who* tends to have confirmed problems. The audit explains why: the labels are confirmed threshold alerts. A model that "detects" them from the same readings is circular, and the earlier version of this project made that mistake.

**What did not work, and why that matters.**
- A two-state HMM fitted by EM learned a near-permanent unstable state ($P(U \to U) \approx 0.97$). It described patient types rather than episodes and scored below the simple Bayesian rate.
- A belief-based restless-bandit allocation, tested in a pilot, did not beat simpler policies once we only counted events the threshold rule had not already flagged.
- Same-day readings added nothing to the vitals rate.

Reporting these keeps the claims honest and shows that the final design was chosen by evidence.

**Why agents.** The decomposition follows the decisions, not the data sources:
- per-person beliefs that must persist and learn
- one shared, budgeted decision
- one delivery duty on a different clock

Separating them gives isolation that was demonstrated, not just claimed: one agent crashed and the others carried on, with identical outcomes.

---

## 8. Limitations
- **Small cohort:** 41 (vitals) and 56 (activity) patients over three months; the confidence intervals are wide.
- **Labels are confirmed alerts**, not diagnoses. The agitation labels began as outputs of the study's own activity model, so part of what the detector learns is that model (each label was confirmed by a person).
- **No forecasting of new vitals episodes:** they were close to unpredictable.
- **The allocation study is a replay:** a review cannot change what happened next in the data.
- **Replay feedback is the study's record.** Clinician feedback through the API has not been tested with real clinicians.
- **Logistic regression ties with the XGBoost agitation detector.**
- **The vitals forecast is slightly over-confident** at the highest risks.

## 9. Future work
- Agitation forecasting at finer time resolution.
- Using the sleep mat where available.
- A prospective study where clinicians answer the review tasks, giving real feedback and a measure of time saved.
- A hospital setting (e.g. VitalDB) as a second domain for the same agent design.

## 10. Conclusion
ECHOS shows that, on a small and imperfectly labelled real dataset, a useful decision-support system can be built honestly:
- audit the labels first
- use personal Bayesian models that work with little data
- give each agent a real goal, belief and decision
- prove the claims with patient-level evaluation and crash tests

The result is a system that finds most confirmed agitation episodes at one alert per patient-week, ranks patients for review better than the study's own threshold rule, raises urgent alerts within a fraction of a second, and keeps working when one of its agents fails.

---

### Reproduce
```bash
PYTHONPATH=. python scripts/evaluation/label_audit.py
PYTHONPATH=. python scripts/evaluation/vitals_eval.py
PYTHONPATH=. python scripts/evaluation/agitation_eval.py
PYTHONPATH=. python scripts/evaluation/allocation_eval.py
PYTHONPATH=. python scripts/train_v2.py
PYTHONPATH=. python scripts/evaluation/team_e2e.py
```
