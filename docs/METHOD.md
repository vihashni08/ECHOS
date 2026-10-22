# ECHOS v2 — method and results

Integrated home-monitoring and decision support for people living with dementia, built on the TIHM dataset
(Palermo et al., *Scientific Data* 2023). ECHOS is decision support, not a diagnostic tool.

Numbers below are copied from `reports/metrics/v2_*.json`. Rerun the scripts named in each section to reproduce them.

## 1. Data used

| Source | Used for | Size |
|---|---|---|
| Physiology: systolic BP, diastolic BP, heart rate | Vitals model | 41 patients with ≥ 14 BP days; 2,201 calendar patient-days, 1,655 with a reading |
| Physiology: temperature, body water | Safety rules only | Same patients |
| Activity (room and door sensors) | Agitation detector | 1,030,559 events → 8,516 windows (each 4–14 h), 56 patients |
| Labels: Blood pressure + Pulse | Vitals target | 280 event days in the cohort |
| Labels: Agitation | Agitation target | 135 labels, 27 patients |

**Not used:**
- Sleep: 17 patients only.
- Skin temperature: 13 patients.
- Weight / temperature / body-water labels: 4, 1 and 68 labels, and only 13 of the 68 have a same-day reading.
- Falls: TIHM has no falls.

The inclusion rule (≥ 14 BP days) was fixed before any result was seen.

**What a label is.** Each label is an alert from the study's rules or model that the monitoring team confirmed as a true event. Labels are confirmed events, not diagnoses.

## 2. Label audit (`scripts/evaluation/label_audit.py`)

| Finding | Number |
|---|---|
| Days with sys ≥ 160 or dia ≥ 100 that carry a BP label | 88% |
| Share of all BP labels that fall on such days | 77% |
| BP labels with a reading within 1 h | 92% |
| Agitation label timestamps | Only 08:00, 09:00, 12:00, 18:00 (the study's batch times) |
| New vitals episodes (no label or crossing in the previous 7 days) predicted 1–3 days ahead | PR-AUC 0.12–0.15 vs chance 0.10 |

**Consequences:**
1. Same-day vitals "detection" would just re-learn a threshold. The threshold therefore stays a **rule** (URGENT), not a model.
2. ECHOS does **not** claim to forecast new vitals episodes.
3. Agitation is scored on the same windows the study used.

## 3. Algorithms

### 3.1 Personal baseline: Normal-Inverse-Gamma (`echos/monitor/personal_baseline.py`)
For each patient and each vital, readings are modelled as $x \sim N(\mu,\sigma^2)$ with prior $(\mu,\sigma^2) \sim NIG(\mu_0,\kappa_0,\alpha_0,\beta_0)$.

The prior is fitted on training patients by moment matching:
- $\mu_0$ = mean of the patient means
- $\beta_0/(\alpha_0-1)$ = within-patient variance
- $\kappa_0$ = within-patient variance / between-patient variance

Update after $n$ past readings (last 28), with mean $\bar x$:

$$\kappa_n=\kappa_0+n,\quad \mu_n=\frac{\kappa_0\mu_0+n\bar x}{\kappa_n},\quad \alpha_n=\alpha_0+\tfrac n2,\quad
\beta_n=\beta_0+\tfrac12\sum(x_i-\bar x)^2+\frac{\kappa_0 n(\bar x-\mu_0)^2}{2\kappa_n}$$

The predictive for the next reading is Student-$t(2\alpha_n,\ \mu_n,\ \sqrt{\beta_n(\kappa_n+1)/(\alpha_n\kappa_n)})$.

From it, ECHOS reports each reading as "today vs usual": the personal usual $\mu_n$, $z=(x-\mu_n)/\text{scale}$, and surprise $=-\log p(x)$.

### 3.2 Vitals priority: personal Bayesian event rate (`echos/monitor/risk.py`)
Each patient has a daily event rate $r_i \sim \text{Beta}(a_0,b_0)$. The empirical-Bayes prior comes from the training patients:

$$\mu=\frac{\sum k_j}{\sum n_j},\quad v=\mathrm{Var}_j\!\left(\frac{k_j}{n_j}\right)-\overline{\frac{\mu(1-\mu)}{n_j}},\quad s=\frac{\mu(1-\mu)}{v}-1,\quad a_0=\mu s,\ b_0=(1-\mu)s$$

- **Online estimate:** $\hat r_t=(a_0+k_t)/(a_0+b_0+n_t)$, which starts at the pooled rate and shrinks towards the patient's own rate.
- **3-day forecast:** $P=1-(1-\hat r_t)^3$.
- **95% credible interval:** from $\text{Beta}(a_0+k_t,\ b_0+n_t-k_t)$.

### 3.3 Agitation detector (`echos/monitor/agitation.py`)
- **Windows:** night 18:00–08:00, morning 08:00–12:00, afternoon 12:00–18:00.
- **Features per window:**
  - counts per room and door
  - events per hour
  - room-to-room transitions
  - longest quiet gap
  - short gaps (< 60 s between events)
  - active hours
  - room entropy
- **Personal baseline per time slot:** $z=(x-\text{median}_{prev\,14})/(\text{IQR}_{prev\,14}+1)$, using past windows only.
- **History, strictly before the window:** episodes in the last 21 days, and windows since the last episode.
- **Model:** XGBoost with Platt calibration, fitted on out-of-fold scores inside the training patients.

### 3.4 Review allocation (`echos/monitor/allocation.py`)
Lift over each detector's own base rate:

$$r_i=\max\!\left(\frac{v_i}{\bar v},\ \frac{a_i}{\bar a}\right)$$

Today's K review slots go to the highest $r_i$. A fairness floor lets patients unseen for ≥ 7 days take up to half the slots. Safety-rule (URGENT) cases sit outside the budget.

### 3.5 Safety rules (`echos/monitor/cohort.py: safety_flags`)
These are TIHM's own daily-check limits (Table 5):

| Measurement | Alert when |
|---|---|
| Systolic | < 80 or > 190 mmHg |
| Diastolic | < 50 or > 110 mmHg |
| Pulse | < 55 or > 100 bpm |
| Temperature | < 35 or > 37.6 °C |
| Body water | < 40% or > 70% |

No model is involved. A crossing sends an URGENT alert immediately.

### 3.6 Episode tracker: two-state hidden Markov model (`echos/monitor/episode.py`, ablation)
- **States:** stable / unstable.
- **Fitting:** Baum-Welch (EM) on the label sequences.
- **Readings:** enter as a scaled likelihood ratio $L_t=\frac{q_t/(1-q_t)}{\rho/(1-\rho)}$.
- **Forecast:** exact propagation of the 2-state chain.

EM learned a near-permanent unstable state ($b\approx0.97$). The HMM therefore describes *which patients* have events more than *when*, and it scores below the simple Bayesian rate. It is kept as an ablation.

## 4. Evaluation protocol
- **Splits:** 5-fold cross-validation split **by patient** (`echos/data/splits.py`). Priors, EM, models and calibration are fitted on the training patients only. Every score is out-of-fold.
- **Budget:** 1 alert per patient-week (7 days, or 21 agitation windows).
- **Metrics:**
  - PR-AUC (the main metric, because events are rare)
  - ROC-AUC
  - recall and precision at the budget
  - false alerts per patient-week
  - Brier score and calibration deciles
- **Uncertainty:** 95% CIs from 1,000 patient-level bootstrap resamples. Paired bootstrap for differences; "better" only when the whole CI is above 0.

## 5. Results

### 5.1 Vitals: validated BP / pulse event on days t+1..t+3 (`scripts/evaluation/vitals_eval.py`)
41 patients, 2,078 patient-days, 589 positive days (prevalence 0.28).

| Method | PR-AUC [95% CI] | ROC | Recall | Precision | False alerts / pt-week |
|---|---|---|---|---|---|
| Persistence (label today) | 0.588 [0.40–0.70] | 0.738 | 0.33 | 0.65 | 0.35 |
| TIHM safety threshold today | 0.569 [0.38–0.69] | 0.742 | 0.34 | 0.67 | 0.33 |
| Personal label rate (no pooling) | 0.611 [0.42–0.72] | 0.744 | 0.37 | 0.73 | 0.27 |
| XGBoost (same features + history) | 0.597 [0.42–0.70] | 0.743 | 0.38 | 0.74 | 0.26 |
| Ablation: HMM + readings + personal onset | 0.531 [0.39–0.62] | 0.712 | 0.33 | 0.65 | 0.35 |
| Ablation: Bayesian rate + today's readings | 0.616 [0.44–0.72] | 0.772 | 0.37 | 0.74 | 0.26 |
| **ECHOS personal Bayesian rate** | **0.619 [0.43–0.72]** | **0.767** | 0.37 | 0.72 | 0.28 |

- **Significantly better than:**
  - the TIHM threshold: +0.050 [0.016, 0.077]
  - the population HMM: +0.064 [0.005, 0.100]
  - the HMM with readings: +0.111 [0.012, 0.197]
- **Ties with** XGBoost and the un-pooled rate, with fewer assumptions and a credible interval.
- **New patients, first 7 days:** 0.572 vs 0.522 for the un-pooled rate. First 21 days: 0.663 vs 0.630.
- **Readings add nothing beyond history.** This is consistent with the audit: the labels are the threshold.

### 5.2 Agitation: confirmed episode in this window (`scripts/evaluation/agitation_eval.py`)
56 patients, 8,516 windows, 135 episodes (prevalence 0.016).

| Method | PR-AUC [95% CI] | ROC | Recall | Precision | Caught |
|---|---|---|---|---|---|
| Time slot only | 0.022 [0.01–0.04] | 0.622 | 0.03 | 0.01 | 4 |
| Episode history only | 0.067 [0.03–0.12] | 0.787 | 0.27 | 0.09 | 37 |
| Activity counts, no personal baseline | 0.227 [0.18–0.28] | 0.942 | 0.56 | 0.18 | 75 |
| Logistic regression (same features) | 0.283 [0.19–0.38] | 0.953 | 0.59 | 0.19 | 79 |
| **ECHOS agitation detector** | **0.270 [0.20–0.35]** | **0.957** | **0.65** | 0.22 | **88 / 135** |

- **The personal baseline adds a significant gain:** +0.042 [0.005, 0.088] over counts alone.
- **Logistic regression ties** (−0.013 [−0.073, 0.051]). XGBoost is kept because it is calibrated (Brier 0.013 vs 0.073).
- **Robust:** without the patient with the most labels (33), PR-AUC is 0.251.

### 5.3 Review allocation, K reviews per day (`scripts/evaluation/allocation_eval.py`)
56 patients, 92 days, about 40 active patients per day. Each row shows agitation followed up the same day / vitals event anticipated 1–3 days ahead / events per review.

| K = 4 | Agitation | Vitals | Events / review | Never reviewed |
|---|---|---|---|---|
| Random | 0.11 | 0.27 | 0.250 | 5 |
| Round robin | 0.12 | 0.28 | 0.260 | 7 |
| Vitals only | 0.04 | 0.49 | 0.393 | 29 |
| Agitation only | 0.64 | 0.16 | 0.368 | 23 |
| Probability union | 0.11 | 0.50 | 0.429 | 28 |
| **ECHOS (lift)** | **0.61** | 0.29 | **0.452** | 22 |
| ECHOS + fairness (7 d) | 0.51 | 0.30 | 0.424 | **8** |

- ECHOS gets 1.8× the events per review of random, and covers both conditions.
- The fairness floor cuts never-reviewed patients from 22 to 8 at about 6% fewer events per review.

## 6. Limitations
- **Small cohort:** 41 / 56 patients over 3 months, so CIs are wide.
- **Labels are confirmed alerts:** they come from the study's own rules and models, so the agitation detector partly re-learns the study model (each label was confirmed by a person).
- **No forecasting of new vitals episodes.**
- **The allocation study is a replay:** a review cannot change what happened in the data.
- **Logistic regression ties with the XGBoost agitation detector.**
