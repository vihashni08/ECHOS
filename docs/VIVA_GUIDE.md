# ECHOS v2: viva guide

Written for defending the project in a viva. Each part gives **plain language first, then technical detail, then where to read the code**.
Every number below was checked against `reports/metrics/v2_*.json` (or against `models/v2/meta.json`) in this session. Where I could not check something, it is marked **[not verified]**.

Ground rules to say out loud in the viva: ECHOS is **decision support, not diagnosis**; **no model decides an emergency**; weak results are called weak.

---

## 0. The 60-second version

A small clinical team can look at only a few people a day. ECHOS reads each person's home-monitoring data (blood pressure, pulse, room-sensor activity), learns what is **usual for that person**, and each day answers: **who should the team look at, and why?** Dangerous readings (red flags) bypass all models and go straight to the caregiver and clinician as an urgent alert.

Three cooperating software agents do this: a **Patient Monitor** (one per person), a **Review Coordinator** (shares K = 4 reviews a day across everyone), and a **Care agent** (delivers alerts, opens review tasks, feeds clinician outcomes back to the monitors).

Headline results (out-of-fold, split by patient, patient-bootstrap 95% CIs):

| Part | Result | Chance / comparison |
|---|---|---|
| Agitation detector | PR-AUC 0.270 (0.196–0.354); 88 of 135 episodes caught at 1 alert per patient-week | chance 0.016 |
| Vitals review priority | PR-AUC 0.619 (0.433–0.718) | chance 0.283; beats the study's own threshold rule by +0.050 (0.016–0.077) |
| Review Coordinator (K = 4) | 0.452 confirmed events per review | random 0.250 |
| URGENT alert latency | 0.29 s median, reading → queued | kill an agent: 0 lost, 0 extra alerts |

---

## 1. The problem, and why v1 became v2

**Plain language.** Home monitoring of people with dementia produces far more readings than clinicians can read. Asking "is this number abnormal?" is easy (a threshold answers it). The hard, useful question is "of 40 people today, whom do I look at first, and why?"

**The story of the refactor** (from `docs/SESSION_CONTEXT.md`, which is in the main checkout, not this worktree):

1. **The examiner's criticism of v1:** "you just feed input to a model and get output; where is the algorithm?" and "your agents are not really agents".
2. **We tested our own v1 before changing anything.** The v1 vitals model scored PR-AUC about 0.70, but raw systolic / diastolic / heart rate alone scored about 0.71. The model was **re-learning the label rule**, not finding anything new. (This v1 figure comes from the session notes; the v1 metrics are in git tag `v1-before-refactor`. The v2 repo's own label audit confirms the cause below.)
3. **Why it was circular (the label audit, `scripts/evaluation/label_audit.py` → `v2_label_audit.json`):**
   - 88% of days with systolic ≥ 160 or diastolic ≥ 100 carry a BP label, and those days hold 77% of all BP labels.
   - Median reading on labelled days is 163/91; on unlabelled days 136/74.
   - 92.5% of BP labels have a reading within 1 hour (`share_with_a_reading_within_1h` = 0.9246).
   - A TIHM "label" is an alert from the study's rules or model that the monitoring team **confirmed**. So the BP labels are largely confirmed threshold alerts. A model that "detects" them from the same day's readings is learning the threshold.
4. **New episodes are close to unpredictable:** 41 onsets; PR-AUC for 1–3-day-ahead prediction 0.12–0.15 against chance 0.104. So ECHOS makes **no forecasting claim**.
5. **v1 "agents" were pipeline stages** (no goals, memory or decisions of their own).
6. **What was removed and why:**
   - Fall detection, SisFall, injected falls: TIHM has no falls; SisFall falls are scripted, mostly young volunteers, and were injected into TIHM timelines, so results were a pipeline check, not clinical evidence.
   - The LLM report stack, knowledge base, triage, investigator, planner, live emulator and ingestion: they did not survive the "is this an algorithm / an agent?" test, and LLM output is hard to verify.
   - All of it is kept in git under tag `v1-before-refactor`.
7. **What was kept and changed:** the alert lifecycle and dispatcher (outbox, retries, escalation), the supervisor/watchdog, the API/web shell. New: label audit, three Bayesian/ML methods, the review-allocation rule, and three genuine agents.

**Technical hook for the viva:** the single most defensible sentence is *"We audited the labels first, found they were confirmed threshold alerts, so we stopped claiming same-day detection, left thresholds as rules, and re-targeted the system to review prioritisation, where the label-validity problem does not apply."*

---

## 2. Novelty: what is new and what is not (be honest)

**Established methods (not novel, say so):**
- Conjugate Normal-Inverse-Gamma and Beta-Bernoulli Bayesian updating; empirical-Bayes prior fitting by moment matching.
- XGBoost, Platt scaling, logistic regression, HMM with Baum-Welch.
- NEWS2 red scores and NICE NG136 severe hypertension as alert rules.
- Bootstrap CIs, GroupKFold, PR-AUC.

**What is genuinely new (modest, defensible):**
1. **A label-validity audit of TIHM** that shows which claims the dataset can and cannot support (`v2_label_audit.json`). Most TIHM work uses the labels as ground truth.
2. **A personal time-of-day baseline for agitation** (z-score against the person's own previous 14 same-slot windows), with a **statistically significant** gain: +0.042 PR-AUC (CI 0.005–0.088) over the same counts without it. Evaluated strictly by patient.
3. **A lift-based allocation rule** that puts two detectors on one scale (`max(v/v̄, a/ā)`) with a fairness floor, chosen against six alternatives in a replay study. Plain probability union let the large vitals probabilities crowd out agitation (agitation followed up: 0.11 vs 0.61).
4. **The combination:** per-person agents with persistent Bayesian beliefs that learn from clinician feedback, one shared budgeted decision agent, and an alert agent on a separate clock, tested with real processes and a SIGKILL test.

**What is not novel / do not oversell:** no new algorithm; the integration and the honest evaluation are the contribution. Note the 2025 arXiv paper benchmarking TIHM agitation prediction is cited by number only and needs verification (open item in the notes).

---

## 3. Research gaps (from your review deck, `ECHOS_Project_Review.pptx`) and what v2 does about each

Your survey has 7 papers; four PDFs were checked against the deck's table (numbers match: MARL 88.3% vs PPO 79.1% / A2C 76.4% / Double DQN 81.6%; IEEE Access 94 / 88 / 84% on MedQA / PubMedQA / MedBullets; CDM 59% vs 56% mortality accuracy, LOS error 4.37 vs 5.82 days, transparency 85.5 vs 86.2; the JBI 2020 paper reports no quantitative metric and asks the physician's consent). The other three (multi-agent review 2026, MVME 2025, LungChat 2026) are cited in the deck only.

| Paper (deck) | What it does | Its own gap, as your deck states it | Where ECHOS v2 differs |
|---|---|---|---|
| Lanza et al., J Biomed Inform 2020 | BDI agents (Jason): robot, virtual assistant, deliberation controller; asks the physician's consent | Scenario-based, simulated inputs, no data-driven prediction | v2 uses real TIHM data and data-driven baselines. But its agents are **not BDI**: ECHOS agents are reactive with memory and a clinician review step. Own this. |
| Multi-agent review 2026 | Reviews LangGraph / CrewAI / AutoGen; 15-50x tokens, error cascades | Needs deterministic orchestration, guardrails, prospective validation | v2 orchestration is deterministic (supervisor, bus, fixed policy); the LLM only writes wording and is validator-gated. No prospective validation. |
| Chen et al., arXiv 2504.03699 (CDM.pdf) | 7 GPT-4o agents on eICU: MAS 59% vs 56% | Modest accuracy, ICU, no home monitoring | v2: no LLM in any prediction; home setting; but a much smaller, single cohort. |
| Öğdü et al., IEEE Access 2025 | BioBERT RAG + web search + confidence loop; 94 / 88 / 84% | Benchmark questions only, compute-heavy, external evidence, needs clinical validation | v2 retrieves from a small **local** guideline index (offline-capable) and checks the text with a validator. It has **no benchmark accuracy**, and no web search by design. |
| MVME, arXiv 2503.16547 | RL-guided consultation flow | Simulated consultations, no monitoring or alerting | v2 monitors real home data and alerts. |
| Shaik et al., Brain Informatics 2025 | One deep-RL agent per vital; 88.3% | 15-subject cohorts, no alert delivery, acknowledgement or explanation | v2 has personal baselines, delivery with acknowledgement and escalation, and explanations. It does not use RL and is not compared head to head with it. |
| LungChat 2026 (preprint) | Supervisor + specialist agents; hierarchy blocks 90.2% of bad tool calls | Preprint, discovery not monitoring | Supports the idea of a hierarchy / a fixed policy in v2. |

**The four overall gaps on slide 5 and the honest status:**

| # | Overall gap (your wording) | v2 answer | How solid |
|---|---|---|---|
| 1 | Studies focus on single functions (monitoring, triage, diagnosis, evidence), mostly in hospitals | One home-monitoring system joining monitoring (Patient Monitor), prioritisation (Coordinator), delivery (Care) and evidence (Evidence agent) | Integration is real and demonstrable. It is one dataset, and not a new kind of component. |
| 2 | Fixed thresholds ignore the individual; little work compares with a person's own usual and explains why an alert fired | Normal-Inverse-Gamma personal usual and z, Beta personal rate, plain-words reasons, cited guideline passage | **Measured** vs the TIHM threshold (+0.050, CI 0.016–0.077) and cold start (0.572 vs 0.522); it **ties** persistence, XGBoost and the un-pooled rate. The explanation quality is **not yet evaluated**. |
| 3 | Few systems join alert delivery, acknowledgement and escalation with validator-checked text kept off the alert path | Care agent lifecycle (NEW → SENT → ACKNOWLEDGED / ESCALATED), outbox with retries, fixed-template urgent mail, Gemini only on the Evidence path with a non-LLM validator, requested after the alert is queued | **Strong on engineering**: 0 lost, 0 extra alerts when an agent is killed. Real SMTP was never exercised. |
| 4 | Mostly benchmark or simulated evaluation, limited clinical validation; sensor failures rarely handled | Real TIHM data, patient-level cross-validation with bootstrap CIs, label audit, crash test | Real data and honest evaluation: yes. **Clinical validation: no.** **Sensor failure: partly.** v2 dropped v1's data-quality gate, then added two light rules (`echos/monitor/quality.py`): zero events in a window where the person's same-slot usual is at least 20, and vitals missing for more than 7 days. They add a "data check" note and never change a score. On real TIHM data they flag 7.4% of windows (0.54 probable device-problem days per patient-week, 46 of 56 patients). Sensitivity was tested only on a synthetic outage because TIHM has no ground truth for sensor failures, and the rule cannot tell a dead sensor from a person who is away. |

**What changed since the deck (you must update the slides):** fall detection and SisFall, the Triage / Investigator / Report agents, SBAR daily and weekly reports, the data-quality gate and the v1 vitals result (PR-AUC 0.697 vs 0.261) are gone. The deck's novelty line "Detectors sense, rule-based Triage decides" is now "audit-first, personal, balanced review ranking with verified evidence". The deck's slide 11 claims 608 labels with BP 305, pulse 95; v2 uses BP + Pulse event days (280 in the cohort) and agitation (135).

**Not fixed (say first):** clinical validation, larger cohorts, live data and devices, forecasting new vitals episodes, a validated sensor-failure detector (only a light rule exists), a BDI-style deliberative agent.

---

## 4. The data

**Source:** TIHM (Technology Integrated Health Management; Palermo et al., *Scientific Data* 2023): 56 people living with dementia, 2019-04-01 to 2019-06-30.

| Used | For | Size (verified) |
|---|---|---|
| Physiology: systolic, diastolic, heart rate | Vitals model | **41 patients** with ≥ 14 BP days; **2,201** calendar days, **1,655** with a reading (`v2_label_audit.json` cohort) |
| Physiology: temperature, body water | Alert rules only | same patients |
| Activity (room and door sensors) | Agitation detector | **1,030,559 events → 8,516 windows**, 56 patients (`v2_agitation.json`) |
| Labels: Blood pressure + Pulse | Vitals target | **280 event days** in the cohort |
| Labels: Agitation | Agitation target | **135 labels, 27 patients** |

**Not used, with reasons:** Sleep (17 patients only); skin temperature (13 patients); weight / temperature / body-water labels (4, 1 and 68 labels; only 13 of the 68 have a same-day reading); falls (TIHM has none). The ≥ 14-days inclusion rule was fixed before any result was seen (config `monitor.min_bp_days`).

**Derived tables:**
- Vitals cohort (`echos/monitor/cohort.py::vitals_cohort`): one row per calendar day between a patient's first and last BP reading; daily **max** of BP/HR (labels are about the most extreme reading), daily mean of temperature/weight/water; `event` = a BP or Pulse label that day.
- Vitals target (`add_targets`): `y_ahead` = any event on days t+1..t+3, known at the end of day t. 2,078 rows, 589 positive (prevalence 0.283).
- Agitation windows (`echos/monitor/agitation.py`): night 18:00–08:00 (14 h), morning 08:00–12:00, afternoon 12:00–18:00; 8,516 windows, prevalence 0.0159.

**What a label is:** an alert from the study's rules or model that the study's monitoring team **confirmed** as a true health event. It is a confirmed event, not a diagnosis, and for BP it is mostly "a threshold crossing a human agreed with". Agitation labels have timestamps only at 08:00 (10), 09:00 (1), 12:00 (79), 18:00 (45): the batch times of the study's own agitation model. So part of what the detector learns is that model.

**Read in order:** `echos/data/loaders.py`, `echos/monitor/cohort.py`, `echos/data/guard.py` (the real-source guard: training tables carry `source='tihm'`), `docs/DATA_STATEMENT.md`, then `scripts/evaluation/label_audit.py`.

---

## 5. The algorithms

### 5.1 Normal-Inverse-Gamma personal baseline (`echos/monitor/personal_baseline.py`)

**Plain:** for each person and each vital, keep a running idea of "what is usual for them" and how much they vary. A new person starts at the population's usual and becomes personal after a few readings. Then, today's reading is described as "systolic 172 vs usual 138 (+3.1 SD)".

**Model:** readings x ~ N(μ, σ²); prior (μ, σ²) ~ NIG(μ₀, κ₀, α₀, β₀).

Prior, fitted on **training patients only** (`fit_prior`):
- μ₀ = mean of patient means ("where the population sits").
- β₀/(α₀ − 1) = mean within-patient variance ("how much a person fluctuates"); α₀ fixed at 3.
- κ₀ = within-patient variance / between-patient variance of means, clipped to [0.5, 20] ("how many readings the population mean is worth"). Fitted κ₀ values are 0.5–1.8 (`models/v2/meta.json`), so a person's own readings dominate within about two readings.

Update after n past readings (the last 28) with mean x̄ (`posterior`):

- κₙ = κ₀ + n: confidence in the mean grows with data.
- μₙ = (κ₀μ₀ + n·x̄) / κₙ: a weighted average of population and personal mean.
- αₙ = α₀ + n/2: more data, more certain about the spread.
- βₙ = β₀ + ½Σ(xᵢ − x̄)² + κ₀n(x̄ − μ₀)² / (2κₙ): prior spread + observed spread + a penalty when the person's mean disagrees with the population's.

Predictive for the next reading: Student-t with 2αₙ degrees of freedom, location μₙ, scale √(βₙ(κₙ+1)/(αₙκₙ)) (`predictive`). The heavy tails mean a new person's baseline is appropriately wide.

Outputs: personal **usual** (μₙ), **z** = (x − μₙ)/scale, **surprise** = −log p(x). Only **past** readings are used (`personal_scores` scores reading t from readings before t).

**Where it is used:** to *explain* a reading in the review list. It does not drive urgent alerts (fixed rules do) and the audit shows same-day readings add nothing to the vitals rate.

**Code to read:** `fit_prior` → `posterior` → `predictive` → `personal_scores`; then `patient_monitor.py::score_vitals` (the online version).

### 5.2 Beta-Bernoulli personal event rate (`echos/monitor/risk.py`)

**Plain:** each person has a daily chance of a confirmed BP/pulse event. Start from the population rate; as days pass with or without events, move toward the person's own rate. Then "chance of an event in the next 3 days" is read off.

- rᵢ ~ Beta(a₀, b₀). Prior by method of moments across training patients (`fit_beta_prior`):
  - μ = Σk/Σn (pooled rate),
  - v = Var(kⱼ/nⱼ) − mean(μ(1−μ)/nⱼ) (between-patient variance **beyond** binomial noise),
  - s = μ(1−μ)/v − 1 (prior strength in pseudo-days), a₀ = μs, b₀ = (1−μ)s.
- Online: r̂ₜ = (a₀ + k)/(a₀ + b₀ + n).
- 3-day risk: **P = 1 − (1 − r̂)³** (probability of at least one event in 3 independent days).
- 95% credible interval: from Beta(a₀ + k, b₀ + n − k).

Verified: the fitted prior strength a₀ + b₀ is **2.1–5.3 pseudo-days** across folds (a₀ ≈ 0.29–0.65, b₀ ≈ 1.8–4.6), i.e. weak prior, so a new patient moves to their own rate within days.

**Honest reading of the result:** it ranks *who tends to have confirmed events*. It is not significantly better than persistence ("label today", +0.031, CI −0.0003 to 0.055), the un-pooled rate (+0.008), or XGBoost (+0.022); it **is** significantly better than the TIHM threshold (+0.050, CI 0.016–0.077) and every HMM variant. Its advantages are fewer assumptions, an interpretable credible interval, and better cold start.

**Code:** `fit_beta_prior`, `PersonalRisk` (the class the Patient Monitor holds), `credible_interval`.

### 5.3 Agitation detector (`echos/monitor/agitation.py`)

**Plain:** for each 4–14 hour window, summarise movement around the home, compare it to what is normal for that person at that time of day, add whether they had recent episodes, and let a tree model score the chance a confirmed episode happened in that window.

1. **Features per window** (`window_features`): counts per location (8 rooms/doors), total, events per hour, room-to-room transitions, door openings, longest quiet gap, short gaps (< 60 s), hours active, room entropy. Silent windows are filled in (a silent window is information).
2. **Personal same-slot baseline** (`add_personal_slot_z`): z = (x − median of previous 14 same-slot windows) / (IQR + 1). Needs ≥ 4 earlier windows; past only (`shift(1)`). The "+1" avoids division by a zero IQR; median/IQR are robust to outliers.
3. **History** (`add_history`): confirmed episodes in the last 21 days, windows since the last episode, strictly before the window.
4. **Model** (`AgitationModel`): XGBoost, 300 trees, depth 3, learning rate 0.03, subsample 0.8.
5. **Platt calibration:** a logistic regression on the log-odds of **out-of-fold** training scores (inner 3-fold GroupKFold by patient), so probabilities are honest (Brier 0.0131; top-decile predicted vs observed close). Fitting calibration on the same scores the model trained on would be over-confident.
6. **Reasons** (`top_reasons`): the features whose personal z is highest, in words ("fridge door 62 (usual-adjusted z +4.4)").

**Results (verified):** PR-AUC 0.270 (0.196–0.354), ROC 0.957, recall 0.652 (88/135), precision 0.217, 0.78 false alerts per patient-week at the budget. Ablations: time slot only 0.022; history only 0.067; counts without personal baseline 0.227; personal z alone 0.072; without history 0.245; + same-day vitals 0.261 (no gain); logistic regression 0.283 (tied, −0.013, CI −0.073 to 0.051). Without the patient who has 33 labels: PR-AUC 0.251. By slot: night 0.251 (only 11 positives), morning 0.245, afternoon 0.349.

### 5.4 Allocation (`echos/monitor/allocation.py`)

**Plain:** the two detectors speak in different units (vitals 3-day risk is around 30%, agitation window probability around 4%). Dividing each by its own base rate says "how many times above normal is this person?", so the two compare fairly. Take the bigger of the two lifts.

- r = max(v/v̄, a/ā), with v̄ = 0.304 and ā = 0.0363 (from out-of-fold scores, `models/v2/meta.json`).
- `select(...)`: sort by r (ties by patient id so it is deterministic); a patient unseen for ≥ 7 days is "due"; due patients may take at most ⌊K × 0.5⌋ slots (highest r first); the rest go to the highest r.
- **K = 4** per day (config `review.k_per_day`); URGENT cases are outside the budget.
- Status written by the Coordinator: REVIEW (selected), WATCH (not selected but ≥ 1.5× a base rate), NORMAL; never downgrades URGENT.

**Results at K = 4 (verified, `v2_allocation.json`), events per review:** random 0.250; round robin 0.260; vitals only 0.393; agitation only 0.368; probability union 0.429; **review past TIHM BP limits 0.471** (added later; agitation followed up only 0.08); review whoever is >= 160/100 0.440; **ECHOS lift 0.452** (agitation 0.61); ECHOS + fairness 0.424; oracle (looks at the future) 0.717. Fairness cut never-reviewed patients from 22 to 8 at ~6% fewer events per review. Known weaknesses: ECHOS gets fewer vitals events anticipated (0.29) than vitals-only (0.49) or probability union (0.50); it is far from the oracle; the study is a replay (a review cannot change the outcome); **no CIs on the allocation numbers** (it is deterministic over a fixed 92-day replay).

### 5.5 Alert rules (`echos/monitor/cohort.py`)

No model. Two tiers (limits in `config.yaml` → `monitor.urgent`, `monitor.attention`):

- **URGENT** (`urgent_flags`, emails caregiver + clinician): systolic ≤ 90 (NEWS2 red); pulse ≤ 40 or ≥ 131 (NEWS2 red); temperature ≤ 35.0 or ≥ 39.1 (NEWS2); BP ≥ 180 / 120 (NICE NG136 severe hypertension). Fires on **3.5%** of reading days (**0.18 per patient-week**); the study confirmed an event on **86%** of those days.
- **Attention** (`safety_flags`, shown only in review reasons): TIHM Table 5 limits (systolic < 80 or > 190, diastolic < 50 or > 110, pulse < 55 or > 100, temperature < 35 or > 37.6, body water < 40% or > 70%). Fires on 11.5% of days (0.60 per patient-week) with 60% confirmed, which is why it is not allowed to email.

Why two tiers: an emergency channel must be rare and trusted. The TIHM limits fire three times as often with lower precision, which would cause alert fatigue.

Check in code: `urgent_flags` uses inclusive limits (`<=`, `>=`). Read it against the config so you can answer "what exactly is 180/120?" (systolic ≥ 180 **or** diastolic ≥ 120).

### 5.6 Ideas tried and rejected (be proud of these)

- **Two-state HMM** (`echos/monitor/episode.py`, ablation): states stable/unstable; fitted by Baum-Welch (EM) on label sequences; readings enter as a scaled likelihood ratio; exact 2-state forecast over 3 days. EM learned an almost permanent unstable state (P(U→U) ≈ 0.98 in fold 0: a = 0.0026, b = 0.9786). So it describes *which patients* are unstable, not *when*, and loses to the simple Beta rate (0.508–0.562 vs 0.619; the differences vs the population HMM, HMM + readings and HMM + readings + personal onset are significant; HMM + personal onset is borderline, CI lower bound −0.00007). Kept as an ablation.
- **Restless bandit (Whittle index) allocation:** according to `docs/SESSION_CONTEXT.md` and `docs/REPORT_DRAFT.md` it was tested in a pilot and did not beat simpler policies once only events not already flagged by the threshold were counted. **There is no bandit code left in the v2 tree** (a git-history search for "bandit"/"whittle" finds only the report-draft commit), so you cannot show it; say it was a pilot and report the negative result.
- **Same-day readings added to the vitals rate:** no gain (+0.0035, CI −0.034 to 0.043).
- **Logistic regression vs XGBoost for agitation:** tied; XGBoost kept for calibration (Brier 0.013 vs 0.073).
- **Probability union for allocation:** let vitals crowd out agitation (agitation followed up 0.11); replaced by lift.

---

## 6. The three agents

**Design test used in the report:** a component is an agent only if it has its own **goal**, a **persistent belief**, its own **decision**, and **communicates** with others. Data checks, models, alert rules and templates fail the test, so they are tools the agents call.

| | Patient Monitor (one per person) | Review Coordinator | Care agent |
|---|---|---|---|
| **Goal** | Know what is usual for this person; say how much they need a clinician today | Use K reviews/day where they catch most, leaving nobody unseen for long | Every urgent case and assigned review reaches a person; the outcome flows back |
| **Remembers** | Last 28 readings per vital (NIG), Beta counts k/n, last 14 same-slot windows, episode history, pending days (table `patient_agent`) | Last review day and first-seen day per patient (`agent_state`) | Alert lifecycle NEW→SENT→ACKNOWLEDGED/RESOLVED/ESCALATED/EXPIRED (`alert_state`, outbox) |
| **Decides** | Red flag → URGENT now; otherwise its risks and plain-words reasons | Who gets today's K slots | Send, resend or escalate; open review tasks |
| **Learns** | From clinician feedback: updates k, n and episode history | Nothing adaptive (fixed policy, but its memory changes picks) | Nothing statistical (timers) |

**Message flow for one day** (topics are in `echos/team/bus.py` table `agent_message`; replay events in `echos/sim/replay_v2.py`):

1. Replay posts `day` events (that day's readings + activity windows) per active patient.
2. Patient Monitor scores them (`PatientMonitor.on_day`). If a red flag: posts **`urgent`** → Care **before writing anything else**; Care builds the fixed-template alert and queues it.
3. Replay posts `outcome` (what TIHM confirmed that day). Care turns it into **`feedback`** → Patient Monitor.
4. Monitor applies feedback (`on_feedback`: updates k, n, episode windows), then posts **`review_request`** (vitals risk, rate + CI, agitation risk, reasons) → Coordinator.
5. Replay posts `day_close` listing who was active. Coordinator waits for every request, allocates K (`allocate`), writes `review_list` and case status, posts **`assign`** → Care and **`review_decision`** → monitors.
6. Care opens `CLINICIAN_REVIEW` tasks. In live use the clinician answers via `POST /api/v2/reviews/outcome` → `clinician_outcome` → feedback.

**Order is part of correctness:** a Monitor will not score a person's next day until the previous day's outcome has been applied (`_outcome_due`, `waited_enough`), so the replay equals the offline evaluation and runs are deterministic. If a peer is down, an agent waits (up to `review.max_wait_s` = 300 s) rather than working around it.

**Why exactly three:** risk is per person, but the review budget is shared across everyone. A per-person agent cannot see the others, so a separate agent must make the shared decision. Delivery runs on a minutes-long clock (acks, escalation) with a different goal from estimation, so it is a third agent. Fewer would mix jobs; more (e.g. separate vitals and agitation agents) would split one person's belief.

**Runtime mechanics:** separate OS processes (`echos/team/run_agent.py`) under a supervisor (`echos/orchestrator/supervisor.py`): restart on missed heartbeats (3 × 5 s) or a stalled cursor (30 s); watchdog restarts the whole runtime; every agent step is idempotent (`_done`, `insert_ignore`); cursor in `agent_checkpoint`.

**Honest pushback points (own these):**
- They are **reactive, goal-directed agents with persistent memory, message coordination and Bayesian learning**. They are not deliberative or negotiating agents (no planning, no LLM, no bargaining).
- The Coordinator's decision is a **fixed policy** (`allocation.select`), not learned.
- Communication is **mostly one-way** pipelines (monitor → coordinator → care), with feedback closing one loop.
- "Learning" is conjugate Bayesian updating of counts and histories, not neural learning.
- The three agents currently share a SQLite file as the bus; a clean answer to "why not just three functions?" is the **isolation you can demonstrate**: kill one process, the others carry on, results identical (`v2_runtime.json`).
- Notes list "active information-gathering (ask for a re-measurement) and negotiation over the review budget" as honest extensions.

**Code, in this order:** `agent_base.py` → `bus.py` → `patient_monitor.py` (class `PatientMonitor` = belief; `PatientMonitorAgent.handle_event/handle_feedback/step` = behaviour) → `review_coordinator.py::allocate/step` → `care_agent.py::handle_urgent/handle_assign/record_outcome` → `coordinator.py` + `lifecycle.py` (escalation) → `runtime.py` → `orchestrator/supervisor.py` → `orchestrator/watchdog.py`.

---

## 6b. Update: the Evidence agent (RAG) and the new screens

**What it is.** A fourth agent (`echos/team/evidence_agent.py`, tools in `echos/evidence/`). Goal: every urgent alert and every assigned review carries an explanation traceable to a guideline passage, or says plainly that none was found.
- **Retrieval (`corpus.py`):** chunks of NICE NG136 (hypertension) and RCP NEWS2 (394 chunks), TF-IDF unigrams + bigrams, cosine similarity. Local, deterministic, no embedding model. Each reason type is routed to the documents that can support it (agitation only to the dementia guideline NG97, which is not indexed yet, so agitation cards honestly say "no supporting source found").
- **Generation (`explain.py`, `llm.py`):** Gemini receives only the structured reasons and the retrieved passages (no patient id) and must cite passage ids and add no numbers of its own.
- **Validator (`validator.py`, no LLM):** JSON shape, every citation was actually retrieved, every number appears in the facts or a cited passage, no diagnosis / medication wording, length. Fail: a fixed template card.
- **Safety:** Care requests evidence only after the alert is queued; URGENT cards are processed first; answers are cached in `data/knowledge/llm_cache.sqlite`; tests never call Gemini (`ECHOS_EVIDENCE_LLM=off`).
- **Evaluation (`v2_evidence.json`, 37 hand-built cases, annotator Claude, not yet spot-checked by you):** retrieval hit@3 1.00 (hit@1 0.55) against 0.55 for naive retrieval, 6 of 6 correct abstentions; Gemini + retrieval accepted by the validator on 26 of 31; of those 26, 22 supported, 4 partly, 0 unsupported; Gemini without retrieval used diagnosis / treatment wording in 3 of 31. **Be ready to say:** the first run was worse (hit@3 0.81, 1 of 6 abstentions, 68% accepted) and I fixed the routing, the prompt and the facts, and widened two gold categories after seeing results; n is small; the model is stochastic; and this checks traceability, not clinical correctness.
- **Honest limits:** the validator proves traceability to the inputs, not that the guideline is the right one for the person (one demo card cites the dementia guideline next to a sensor-check sentence that actually comes from the facts); only four guidelines are indexed; the top retrieved passage is right 55% of the time.

**New screens:** Morning briefing (three claims, urgent pinned, review cards with evidence and the cutoff), Decision trace (8 steps + memory card; "models trained per patient: 0"), Test it yourself (audit wall, You vs ECHOS on 10 evenly spread days, budget sliders), Under the hood (4 agents, crash diff, risks), Presenter mode.

**A finding you must be ready for:** a plain "review whoever is past TIHM's BP limits" rule gets 0.471 events per review at K = 4, above ECHOS's 0.452, but follows up 8% of agitation episodes against 61% (`v2_allocation.json`). ECHOS is a balanced policy, not a way to find more events in total.

**Extra viva question: "Why does your system need an LLM at all?"** It does not make any decision. It only writes the wording of an explanation from facts and retrieved guideline text, a validator rejects anything not traceable, and the template is the fallback. The contribution is verifiable explanation, not generation.

---

## 7. How it was trained and tested

- **Split:** 5-fold **GroupKFold by patient** (`echos/data/splits.py`, seed 42; patients shuffled then grouped). Rows of one person are never in both train and test. Random-row splitting would leak a person's personal pattern and inflate everything.
- **Everything fitted on training patients only:** NIG priors, Beta prior, XGBoost, Platt calibration (with an inner 3-fold group split), HMM/EM, base rates `v̄`, `ā` (from out-of-fold scores).
- **Out-of-fold (OOF):** every reported score is for a patient whose fold's models never saw them. Pooled OOF scores are then evaluated (`data/v2/oof_*.parquet`, git-ignored).
- **Baselines and ablations:** vitals (7 baselines + 5 HMM/other ablations), agitation (time slot, history only, counts only, personal z only, logistic regression, no-history, + same-day vitals), allocation (random, round robin, vitals only, agitation only, probability union, oracle).
- **Metrics:** PR-AUC primary (events are rare; ROC is flattering at 1.6% prevalence); recall/precision at **1 alert per patient-week** (`metrics.at_budget`); false alerts per patient-week; Brier; calibration deciles.
- **CIs:** 1,000 **patient-level** bootstrap resamples (`bootstrap_ci`: resample patients, keep each patient's rows together). Differences: **paired** patient bootstrap (`paired_diff_ci`); "better" only if the whole 95% CI > 0.
- **Scripts:** `label_audit.py`, `vitals_eval.py`, `agitation_eval.py`, `allocation_eval.py`, `train_v2.py`, `team_e2e.py`. `scripts/rebuild_models.sh` runs them in order.
- **The demo is not in-sample:** `scripts/train_v2.py` saves the 5 fold models plus an "all" model; `echos/monitor/store.py::for_patient` loads the fold that **excluded** the patient (vitals and agitation folds are separate splits because the cohorts differ). Only a patient not in the training data (live mode) would get "all". The demo cohort (8 patients × 28 days) is chosen for having the most events (`replay_v2.py::pick_window`), selection only, never touching the models. The only invented data: `POST /api/sim/reading`, marked injected.
- **Weak spots an examiner can find:** v̄ and ā are population means computed from all out-of-fold scores (constants, a small leak at most); the alert-budget and K were chosen on the same replay; the HMM was tuned less than the winner; no external dataset.

**Code to read:** `splits.py` → `metrics.py` → `scripts/evaluation/vitals_eval.py` (look for how folds, priors and OOF arrays are assembled) → `agitation_eval.py` → `allocation_eval.py` → `scripts/train_v2.py` → `store.py`.

---

## 8. Results and efficiency (all verified from `reports/metrics/v2_*.json`)

### 8.1 Vitals priority: 41 patients, 2,078 patient-days, 589 positives (0.283)

| Method | PR-AUC [95% CI] | Note |
|---|---|---|
| Persistence (label today) | 0.588 [0.40–0.70] | |
| TIHM safety threshold today | 0.569 [0.38–0.69] | |
| BP ≥ 160/100 today | 0.568 [0.40–0.67] | |
| Personal label rate (no pooling) | 0.611 [0.42–0.72] | |
| XGBoost (same features + history) | 0.597 [0.42–0.70] | |
| HMM variants (4) | 0.508–0.562 | all below |
| Bayesian rate + today's readings | 0.616 | no gain |
| **ECHOS personal Bayesian rate** | **0.619 [0.43–0.72]**, ROC 0.767, recall 0.365, precision 0.724, 0.28 false alerts / patient-week | |

- Chance = 0.283. The CIs of all methods overlap heavily; only the paired differences count: **significant** vs TIHM threshold (+0.050, 0.016–0.077) and vs HMM variants; **not significant** vs persistence, un-pooled rate, XGBoost, or + readings.
- **Meaning:** it ranks *who* has confirmed events, at least as well as anything else, with an uncertainty interval and a better cold start. It does **not** detect or forecast new episodes.
- Calibration: slightly **over-confident at the top**: top decile mean forecast 0.867 vs 0.774 observed.

### 8.2 Agitation: 56 patients, 8,516 windows, 135 episodes (0.0159)

PR-AUC 0.270 [0.196–0.354]; ROC 0.957 [0.939–0.970]; recall 0.65 [0.54–0.81]; precision 0.22 [0.14–0.31]; 88 of 135 caught at 406 alerts (1 per patient-week of 405.5 patient-weeks). That is **17× chance PR-AUC**, but precision is 22% (about 4 of 5 alerts at the budget are not confirmed episodes). Gain from the personal baseline: +0.042 [0.005, 0.088], significant but small and the lower bound is barely above zero; logistic regression ties.

### 8.3 Allocation: 56 patients, 92 days, about 40 active/day, 135 agitation + 280 vitals events

K = 4: ECHOS 0.452 vs random 0.250 (1.8×). K = 2: 0.457 (0.495 with fairness) vs 0.270 random. K = 6: 0.424 vs 0.258. No CIs here.

### 8.4 Runtime (`v2_runtime.json`, real processes, 8 patients × 28 days, 470 events)

| Check | Result |
|---|---|
| Patient-days scored / days allocated / reviews | 222 / 28 / 112 |
| Messages left unconsumed | 0 |
| Alerts raised | 21 |
| URGENT latency, reading → alert queued | median 0.288 s, max 0.434 s |
| URGENT latency, reading → dry-run email sent | median 0.295 s, max 0.440 s |
| Whole replay wall time | 31.0 s (33.1 s with the kill) |
| SIGKILL of Patient Monitor at event 239 | detected by missed heartbeats, restarted in **11.9 s** |
| Kill run vs uninterrupted run | **0 lost, 0 extra alerts**; identical review picks, case statuses, days scored |
| Worst-case URGENT latency during the kill | max 10.9 s (the alert that arrived while the monitor was down was delayed, not lost) |

**Why it is efficient:** (a) the urgent path has no model: a red flag is a comparison, the message is posted before any other write, Care handles urgent messages first, the dispatcher sends on background threads with retries; (b) the models are tiny (conjugate updates are a few arithmetic operations; one XGBoost with 300 depth-3 trees) and loaded once per fold from disk (`lru_cache` in `store.py`); (c) agents are idempotent and cursor-based, so a restart redoes only unfinished work; (d) the API never loads a model, it reads SQLite. Say "0.2 s is software latency in one machine with a dry-run mailer"; real SMTP was **never** exercised, and replay days arrive in batches so reading-to-alert in real life would depend on the device cadence.

---

## 9. Limitations and what to say

| Limitation | Say this |
|---|---|
| 41 / 56 patients, 3 months | "CIs are wide and we report them; we call results weak where they are." |
| Labels are confirmed alerts; agitation labels began as the study model's output | "We audited this. It is why we do not claim same-day vitals detection, and why agitation is scored on the study's own windows. Part of the agitation detector re-learns the study model; each label was confirmed by a person." |
| No forecasting of new vitals episodes | "We tested it (PR-AUC 0.12–0.15 vs 0.10 chance). A negative finding that shaped the design." |
| Allocation is a replay | "A review cannot change what happened next. We measure whether the right people would have been picked, not benefit." |
| Feedback in replay is the study record | "In live use it comes from clinicians via the API; that path is tested in software, not with real clinicians." |
| Logistic regression ties XGBoost | "Yes. We kept XGBoost for calibration (Brier 0.013 vs 0.073), not accuracy." |
| Vitals priority ties persistence and XGBoost | "Yes. Its value is cold start, an interval, and fewer assumptions. The significant win is over the study's own threshold." |
| A plain BP-limit rule gets 0.471 events per review, above ECHOS's 0.452 | "True, and we report it (`v2_allocation.json`). The rule is a strong baseline for BP because the labels are threshold alerts, but it follows up 8% of agitation episodes against 61% for ECHOS. ECHOS is a balanced policy for a team responsible for both, not a way to find more events in total." |
| Slight over-confidence at the top vitals decile | "0.87 forecast vs 0.77 observed; we report it." |
| Agitation precision 0.22 | "At 1 alert per patient-week four in five alerts are unconfirmed; it is a ranking aid for review, not an alarm." |
| No live patients, devices, real email | "Dry-run mailer by default; real mail only with `--real --yes`." |
| Agents are reactive | "Goal, memory, messages, learning, but a fixed Coordinator policy." |
| Clinical validity | "Not claimed. Needs a prospective study." |

---

## 10. Web app and 10-minute demo

Pages (`web/src/pages/`): **Today** (`Today.tsx`; ranked review list with plain-words reasons, urgent alerts, caseload), **Review board** (`Review.tsx`; days, outcomes, clinician confirm/dismiss), **Patients** (`PatientPage.tsx`; vitals vs personal usual with dashed usual line, agitation, 3-day risk with base rate, day log, the person's agent messages), **Alerts** (`Alerts.tsx`), **Agents** (`Agents.tsx`; health, heartbeats, kill switch, live message trace), **How it works** (`HowItWorks.tsx`; flow, results from `reports/metrics`, limits). **Demo tools** sheet (`components/DemoTools.tsx`, `SimControls.tsx`, `InjectReading.tsx`): replay speed/play, inject a reading, kill an agent. Chart: `charts/TrendChart.tsx`.

**Demo script** (`docs/DEMO.md`, ~10 min):
1. How it works (1 min): the problem, the label audit sentence.
2. Demo tools → 20 events/s → Play; Agents page: three processes alive, message trace fills (`review_request`, `feedback`, `assign`).
3. Today (2 min): read one reason card aloud; point out fairness picks.
4. Patients → 46286 (2 min): the usual line starts at population and moves to personal; the 3-day risk climbs as feedback arrives.
5. Inject a reading (1 min): "Severe high BP 192/124" → Alerts: red-flag alert queued about 0.2 s later → "I have seen this" stops the escalation timer.
6. Kill the Patient Monitor (1 min): ~12 s later "alive again", restarts 1; nothing lost (`v2_runtime.json`).
7. Results and limits (2 min): headline table; read limits yourself before they ask.

Demo hazards from the notes: keep the Mac awake (replay pauses on sleep; the watchdog resumes it); stop `scripts/start.sh` before running Playwright (ports); Node 20.18 triggers a Vite warning but works.

---

## 11. Reading order (30 files)

1. `CLAUDE.md`, `README.md`, `docs/METHOD.md`, `docs/ARCHITECTURE.md` (30 min).
2. Data: `echos/data/loaders.py`, `echos/monitor/cohort.py`, `scripts/evaluation/label_audit.py` + `reports/metrics/v2_label_audit.json`.
3. Methods (one hour): `personal_baseline.py` (look for `posterior`, past-only scoring), `risk.py` (look for `fit_beta_prior`, `PersonalRisk`), `agitation.py` (look for `shift(1)` in `add_personal_slot_z`, out-of-fold Platt in `AgitationModel.fit`), `allocation.py` (`priority`, `select`), `episode.py` (only skim: ablation).
4. Evaluation: `echos/data/splits.py`, `echos/monitor/metrics.py`, `scripts/evaluation/vitals_eval.py`, `agitation_eval.py`, `allocation_eval.py`, `scripts/train_v2.py`, `echos/monitor/store.py`.
5. Agents: `echos/team/agent_base.py`, `bus.py`, `patient_monitor.py`, `review_coordinator.py`, `care_agent.py`, `coordinator.py`, `runtime.py`, `echos/orchestrator/supervisor.py`, `watchdog.py`, `state.py` (tables).
6. Replay: `echos/sim/replay_v2.py`, `controller.py`.
7. Alerts: `echos/alerts/payload.py` (fixed template, de-identified id + age band), `dispatcher.py` (outbox, retries, cooldown), `mailer.py` (DryRunMailer vs SMTP), `acklink.py`.
8. API: `echos/api/routers/v2.py` (`/api/v2/review`, `/api/v2/patients/{id}/monitor`, `/api/v2/reviews/outcome`, `/api/v2/summary`, `/api/sim/reading`), `docs/API.md`.
9. Tests: `tests/test_monitor.py` (17), `tests/test_team_v2.py` (8, the agents), `tests/test_alerts.py`.
10. Web: pages above.

---

## 12. Commands and what to expect

**Where to run them.** This worktree (`.claude/worktrees/echos-v2-viva-prep-7da1d0`) has the code but **no `.venv`, `web/node_modules`, `data/` or `models/`** (git-ignored). The main checkout `/Users/vihashni08/Documents/ECHOS` is at the same commit (`03ffc09`) and has them all, so run everything there.

```bash
cd /Users/vihashni08/Documents/ECHOS
```

**Tests (verified this session):**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
```

Expected: a line of dots then `72 passed in ~11 s`.

**Reproduce all numbers and models (not run by me: it rewrites `reports/metrics/v2_*.json` and `models/v2/`; takes a few minutes):**

```bash
scripts/rebuild_models.sh
```

Expected: six scripts run in order and the last line is `v2 results and models rebuilt`. Spot-check afterwards: `v2_agitation.json` → `results["ECHOS agitation detector"].pr_auc` ≈ 0.2696; `v2_vitals.json` → `results["ECHOS: personal Bayesian rate"].pr_auc` ≈ 0.619; `v2_runtime.json` → `kill_vs_reference` all zero / true. Bootstrap and fits are seeded, so numbers should match to the printed digits (I have not re-run this to confirm).

**One script at a time:**

```bash
PYTHONPATH=. .venv/bin/python scripts/evaluation/label_audit.py
```

**Playwright (not run by me; notes say 11 tests on :8010/:5180, axe WCAG AA in both themes):**

```bash
cd web && npx playwright test
```

```bash
cd web && npx playwright test -c playwright.auth.config.ts
```

The second suite is 3 tests on :8031/:5201 (per the notes). Stop `scripts/start.sh` first if ports clash.

**Web build and contrast check:**

```bash
cd web && npm run build && node contrast.mjs
```

**Run the demo:**

```bash
scripts/start.sh
```

Expected: API at `http://127.0.0.1:8000`, web at `http://localhost:5173`; top bar shows REPLAY and Live; mail is a dry run. Open **Demo tools** → **Play**. Ctrl-C stops everything. `scripts/start.sh --check` starts, health-checks, and stops (exit 0 = healthy). Real email only with `scripts/start.sh --real --yes`; **never use that in a viva**.

---

## 13. Fifteen viva questions and answers

**1. "Where is the algorithm? Isn't this just a model?"**
There are four: a Normal-Inverse-Gamma personal baseline (conjugate update, empirical-Bayes prior), a Beta-Bernoulli personal event rate with a credible interval, a window-based agitation detector with a personal same-slot z-score and Platt calibration fitted on out-of-fold scores, and a lift-based allocation rule with a fairness floor. Only one of them uses a learned model (XGBoost); the rest are closed-form Bayesian updates written by hand (`personal_baseline.py`, `risk.py`, `allocation.py`).

**2. "Why did you throw away v1?"**
Because we tested it. The v1 vitals model scored PR-AUC ≈ 0.70 while raw vitals alone scored ≈ 0.71, so it was re-learning the label rule. The v2 audit shows why: 88% of days with BP ≥ 160/100 are labelled and 92% of labels are within an hour of a reading. And the v1 "agents" were pipeline stages. We removed falls (TIHM has none; SisFall was injected), the LLM stack and the pipeline (tag `v1-before-refactor`).

**3. "What is a label? Can you trust it?"**
A TIHM alert from the study's rules or model that the monitoring team confirmed. For BP it is largely a confirmed threshold crossing; for agitation it began as the study model's output. So we do not claim same-day detection of BP events and we keep thresholds as rules.

**4. "What is novel here?"**
Not the algorithms: they are established. The contributions are the label audit, the personal time-of-day baseline for agitation with a significant gain, the lift allocation under a daily budget with fairness, and a three-agent runtime evaluated with real processes and a crash test.

**5. "Your vitals PR-AUC is 0.62; chance is 0.28. Is that good?"**
It is above chance and significantly above the study's own threshold rule (+0.050, CI 0.016–0.077), but it ties persistence, the un-pooled personal rate and XGBoost. What it adds is cold start (0.572 vs 0.522 in the first 7 days), a credible interval and fewer assumptions. It ranks who tends to have confirmed events; it does not forecast new episodes (we measured 0.12–0.15 vs 0.10 chance).

**6. "Why PR-AUC and not accuracy or ROC-AUC?"**
Prevalence is 1.6% for agitation: a model that says "never" has 98% accuracy. PR-AUC reflects the precision/recall trade-off on the rare class; ROC-AUC (0.957) looks great and hides a precision of 0.22. We also report recall and precision at a fixed alert budget, and false alerts per patient-week.

**7. "Why GroupKFold by patient? Why out-of-fold?"**
Each person has a personal pattern; random rows would put the same person in train and test and inflate scores. Every score comes from models that never saw that patient, and calibration, priors, base rates were fitted on training patients only. The demo uses the same idea: each patient is scored by the fold model that excluded them (`store.for_patient`).

**8. "Your confidence intervals overlap. How can you claim anything?"**
Overlapping CIs of two methods do not decide it. We use a **paired** patient bootstrap on the difference and call something better only when the whole 95% CI is above zero. That holds for ECHOS vs the TIHM threshold (+0.050) and the personal baseline gain for agitation (+0.042, 0.005–0.088, only just). Everything else we call a tie.

**9. "Why Beta-Bernoulli and not an LSTM or XGBoost?"**
With 41 patients and 280 event days, a sequence model overfits and has no honest uncertainty. The Beta posterior is exact, interpretable (k events in n days), shrinks new patients toward the population, and gives a credible interval. XGBoost with the same features and history ties it (0.597 vs 0.619, CI includes zero). The HMM (EM learned a near-permanent unstable state) and a bandit pilot lost, so we report them as negative results.

**10. "Are your agents really agents?"**
They have a goal, persistent belief (tables `patient_agent`, `agent_state`, `alert_state`), own decisions, message-only communication, and one learning loop (feedback → Patient Monitor). They are reactive, goal-directed agents, not deliberative: the Coordinator runs a fixed policy, there is no negotiation or LLM, and the traffic is mostly one-way. The design test in the report lists these criteria.

**11. "Why exactly three agents?"**
Risk belongs to one person; the K-review budget is shared, so someone who sees everyone must decide; delivery of alerts has a different clock (minutes: acknowledge, escalate) and goal. Merging any two mixes a per-person job with a global one, or estimation with delivery.

**12. "Show that the multi-process design matters."**
`scripts/evaluation/team_e2e.py` runs the whole replay with real processes twice, and SIGKILLs the Patient Monitor half-way in the second. The supervisor detects missed heartbeats and restarts it in 11.9 s; resulting alerts, review picks and case statuses are identical to the uninterrupted run (0 lost, 0 extra). Because each agent is idempotent and checkpointed, restart replays only unfinished work.

**13. "The allocation result is a replay. How do you know it would help?"**
We do not. A review cannot change what happened in the data, so we measure whether reviewed people were the ones with confirmed events: 0.452 per review vs 0.250 random at K = 4. The oracle gets 0.717, so there is headroom. Proving benefit needs a prospective study. Also, the allocation numbers have no CIs and the fairness floor trades about 6% events per review for cutting never-reviewed patients from 22 to 8.

**14. "Is the demo in-sample? Is the data real?"**
All replayed data is real TIHM data. Each person is scored by the cross-validation fold that excluded them. The demo cohort is chosen for having the most events, which is selection for the demo, not for training. The only invented data is the "inject a reading" tool, marked as injected. Feedback in the demo is TIHM's confirmation record, not a clinician.

**15. "What would you do next / what would convince you it works clinically?"**
A prospective pilot where clinicians answer review tasks (`POST /api/v2/reviews/outcome`), so the feedback is real and time-saved can be measured; larger and external cohorts (hospital data like VitalDB were discussed and deferred); finer-resolution agitation forecasting; the sleep mat where available; and, to make the agents more deliberative, active information-gathering and budget negotiation.

---

## 14. Things I could not verify (check before the viva)

- The literature survey and the exact "research gaps" text: not present in this repo; the 5-gap list comes from `docs/SESSION_CONTEXT.md`.
- `docs/SESSION_CONTEXT.md` itself is **untracked in the main checkout** and missing from this worktree. Consider copying it into `docs/` and committing.
- The v1 "0.70 vs 0.71" numbers (from the notes; v1 metrics are in the tag).
- The restless-bandit pilot has no code left; only the notes and report mention it.
- Citations marked [verify] in `docs/REPORT_DRAFT.md` (Mate et al. 2020, the 2025 TIHM agitation arXiv paper, Wooldridge & Jennings 1995, Gelman BDA edition).
- I ran pytest (72 passed). I did **not** run `rebuild_models.sh`, Playwright, the web build, or the live demo.
- Playwright test counts (11 + 3) are from the notes; a quick grep of the spec files did not confirm them.
