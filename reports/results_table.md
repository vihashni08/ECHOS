### Agent 1 (vitals), out-of-fold, patient-level GroupKFold. 1733 reading-days, 54 patients, prevalence 0.182

| Method | PR-AUC (95% CI) | Recall (95% CI) | False alerts / patient-week | Alerts |
|---|---|---|---|---|
| Fixed rule (sys>=140 / dia>=90 / sys<100) | 0.261 (0.183-0.346) | 0.759 (0.617-0.872) | 1.58 | 834 |
| XGBoost @ same alert count as rule | 0.681 (0.561-0.767) | 0.886 (0.819-0.932) | 1.48 | 834 |
| XGBoost @ 0.5 alerts/pt-wk | 0.681 (0.561-0.767) | 0.473 (0.366-0.599) | 0.10 | 187 |
| XGBoost @ 1.0 alerts/pt-wk | 0.681 (0.561-0.767) | 0.698 (0.612-0.793) | 0.41 | 375 |
| XGBoost @ 2.0 alerts/pt-wk | 0.681 (0.561-0.767) | 0.876 (0.802-0.916) | 1.26 | 751 |

SHAP top features: sys 0.72, dia 0.62, hr 0.38, dia_base 0.33, hr_base 0.18

### Agent 2 (behaviour / agitation) vs base rate, out-of-fold; CI = patient bootstrap. 2236 days, 50 patients (27 with an agitation day)

| Metric | Value (95% CI) | Reference |
|---|---|---|
| PR-AUC (full agent) | 0.194 (0.133-0.309) | 4.0x base rate |
| PR-AUC, base rate (no skill) | 0.049 (0.028-0.079) | 1.0x |
| PR-AUC, personal-baseline drift only | 0.044 (0.026-0.070) | 0.9x |
| ROC-AUC (full agent) | 0.852 (0.804-0.900) | chance = 0.5 |
| ROC-AUC, drift only | 0.453 (0.378-0.542) | chance = 0.5 |
| Recall @ 0.5 alerts/pt-wk | 0.336 (0.186-0.502) | 0.38 false alerts/pt-wk |
| Recall @ 1.0 alerts/pt-wk | 0.573 (0.411-0.719) | 0.80 false alerts/pt-wk |
| Recall @ 2.0 alerts/pt-wk | 0.827 (0.722-0.920) | 1.71 false alerts/pt-wk |

Ablation: most signal is raw same-day activity level (agitated days mean 574 events vs 383); personal-baseline drift adds little. Drift-only score is at chance (ROC 0.45).

**Agent 2 headline (within-patient, 27 patients with both classes; median across patients):** ROC-AUC 0.814 (IQR 0.692-0.944), PR-AUC 0.386 (IQR 0.143-0.620) (median per-patient prevalence 0.040). Pooled numbers above mix between-patient differences in activity level with within-patient signal.

| Model | Pooled ROC-AUC | Within-patient ROC-AUC | Pooled PR-AUC | Within-patient PR-AUC |
|---|---|---|---|---|
| Full feature set | 0.852 | 0.814 (IQR 0.692-0.944) | 0.194 | 0.386 (IQR 0.143-0.620) |
| Per-patient z-scores only (no absolute levels) | 0.664 | 0.827 (IQR 0.621-0.956) | 0.089 | 0.309 (IQR 0.122-0.442) |
| Drift score only | 0.453 | 0.500 (IQR 0.333-0.642) | 0.044 | 0.091 (IQR 0.044-0.214) |

Leakage audit: every baseline feature (z, CUSUM, drift, deltas) uses shift(1) past-only windows; raw same-day counts are the current observation, as is the same-day label. Top gain importance: total_events 0.19, room_transitions 0.13, total_events_delta 0.05, kitchen_cusum 0.04, night_bathroom_cusum 0.04, fridge_z 0.04, fridge 0.04, night_events 0.04, room_transitions_z 0.03, kitchen_z 0.03. Dropping absolute levels cuts pooled ROC 0.874 to 0.660 but within-patient ROC stays ~0.86: personal-baseline z-scores carry the within-patient signal; absolute levels mostly separate patients.

### Agent 3 (falls, SisFall), subject-wise out-of-fold, threshold 0.5. Overall PR-AUC 0.995, 38 subjects

| Group | Fall subjects | Fall trials | Sensitivity (Wilson 95% CI) | Specificity (windows) | False alarms / hour |
|---|---|---|---|---|---|
| adult | 23 | 1722 | 0.985 (0.978-0.990) | 0.9989 | 2.32 |
| elderly | 1 | 75 | 0.853 (0.756-0.916) | 0.9995 | 1.00 |

Wilson CIs treat trials as independent; trials are clustered within subjects, so true uncertainty is wider (elderly = one subject).

### End-to-end replay (systems run, full period, injected falls)

| Measure | Value |
|---|---|
| Patients / patient-days / patient-weeks | 20 / 1266 / 180.9 |
| Injected falls | 60 |
| Fall detection rate (URGENT, fall reason) | 0.950 (0.863-0.983) (57/60) |
| Detection delay, simulated (fall -> end of day), median / max | 11.4 h / 23.8 h |
| Pipeline latency after last agent output, median | 0.21 s |
| False fall alerts per patient-week | 0.066 (12 alerts) |
| URGENT days from critical vitals (real TIHM readings) | 4 |
| All URGENT alerts per patient-week | 0.404 |
| Days Normal / Monitor / Urgent | 65.6% / 28.7% / 5.8% |
| Days with a data gap | 4.5% |
| Duplicate alerts | 0 |

### Fault injection (3 patients x 15 days; clean run 181.6 s)

| Fault | Lost alerts | Extra alerts | Duplicates | Falls alerted | Detect (s) | Recover (s) | Cases identical to clean run | Report modes | Wall overhead (s) |
|---|---|---|---|---|---|---|---|---|---|
| kill physiological | 0 | 0 | 0 | 9/9 | 2.9 | 4.8 | 45/45 | llm:42,template:9 | 2.2 |
| kill behaviour | 0 | 0 | 0 | 9/9 | 2.8 | 4.0 | 45/45 | llm:42,template:9 | 10.7 |
| kill fall | 0 | 0 | 0 | 9/9 | 2.8 | 3.8 | 45/45 | template:9,llm:42 | 8.6 |
| real Gemini client, 1s timeout | 0 | 0 | 0 | 9/9 | - | - | 45/45 | template:47,llm:4 | -10.6 |
| LLM key removed | 0 | 0 | 0 | 9/9 | - | - | 45/45 | template:51 | -168.8 |
