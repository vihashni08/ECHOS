### Agent 1 (vitals), out-of-fold, patient-level GroupKFold. 1733 reading-days, 54 patients, prevalence 0.182

| Method | PR-AUC (95% CI) | Recall (95% CI) | False alerts / patient-week | Alerts |
|---|---|---|---|---|
| Fixed rule (sys>=140 / dia>=90 / sys<100) | 0.261 (0.183-0.346) | 0.759 (0.617-0.872) | 1.58 | 834 |
| XGBoost @ same alert count as rule | 0.697 (0.571-0.782) | 0.886 (0.827-0.938) | 1.48 | 834 |
| XGBoost @ 0.5 alerts/pt-wk | 0.697 (0.571-0.782) | 0.483 (0.378-0.608) | 0.09 | 187 |
| XGBoost @ 1.0 alerts/pt-wk | 0.697 (0.571-0.782) | 0.711 (0.619-0.789) | 0.40 | 375 |
| XGBoost @ 2.0 alerts/pt-wk | 0.697 (0.571-0.782) | 0.879 (0.810-0.920) | 1.26 | 751 |

SHAP top features: sys 0.72, dia 0.62, hr 0.38, dia_base 0.33, hr_base 0.18

### Agent 2 (behaviour / agitation) vs base rate, out-of-fold; CI = patient bootstrap. 2236 days, 50 patients (27 with an agitation day)

| Metric | Value (95% CI) | Reference |
|---|---|---|
| PR-AUC (full agent) | 0.216 (0.143-0.312) | 4.4x base rate |
| PR-AUC, base rate (no skill) | 0.049 (0.028-0.079) | 1.0x |
| PR-AUC, personal-baseline drift only | 0.044 (0.026-0.070) | 0.9x |
| ROC-AUC (full agent) | 0.859 (0.809-0.904) | chance = 0.5 |
| ROC-AUC, drift only | 0.453 (0.378-0.542) | chance = 0.5 |
| Recall @ 0.5 alerts/pt-wk | 0.355 (0.232-0.522) | 0.38 false alerts/pt-wk |
| Recall @ 1.0 alerts/pt-wk | 0.582 (0.424-0.736) | 0.80 false alerts/pt-wk |
| Recall @ 2.0 alerts/pt-wk | 0.836 (0.700-0.942) | 1.71 false alerts/pt-wk |

Ablation (out-of-fold, from scripts/audit_agent2.py): per-patient z-scores only: pooled ROC-AUC 0.658, PR-AUC 0.088; drift score only: pooled ROC-AUC 0.453 (chance = 0.5).

**Agent 2 headline (within-patient, 27 patients with both classes; median across patients):** ROC-AUC 0.818 (IQR 0.655-0.976), PR-AUC 0.449 (IQR 0.146-0.584) (median per-patient prevalence 0.040). Pooled numbers above mix between-patient differences in activity level with within-patient signal.

| Model | Pooled ROC-AUC | Within-patient ROC-AUC | Pooled PR-AUC | Within-patient PR-AUC |
|---|---|---|---|---|
| Full feature set | 0.859 | 0.818 (IQR 0.655-0.976) | 0.216 | 0.449 (IQR 0.146-0.584) |
| Per-patient z-scores only (no absolute levels) | 0.658 | 0.824 (IQR 0.617-0.930) | 0.088 | 0.262 (IQR 0.155-0.500) |
| Drift score only | 0.453 | 0.500 (IQR 0.333-0.642) | 0.044 | 0.091 (IQR 0.044-0.214) |

Leakage audit: every baseline feature (z, CUSUM, drift, deltas) uses shift(1) past-only windows; raw same-day counts are the current observation, as is the same-day label. Top gain importance: total_events 0.19, room_transitions 0.13, total_events_delta 0.05, kitchen_cusum 0.04, night_bathroom_cusum 0.04, fridge_z 0.04, fridge 0.04, night_events 0.04, room_transitions_z 0.03, kitchen_z 0.03. Dropping absolute levels moves pooled ROC 0.859 to 0.658, while within-patient ROC moves 0.818 to 0.824: absolute levels mostly separate patients; personal-baseline z-scores carry the within-patient signal.

### Agent 3 (falls, SisFall), subject-wise out-of-fold, threshold 0.8. Overall PR-AUC 0.997, 38 subjects

| Group | Fall subjects | Fall trials | Sensitivity (Wilson 95% CI) | Specificity (windows) | False alarms / hour |
|---|---|---|---|---|---|
| adult | 23 | 1722 | 0.918 (0.904-0.930) | 0.9983 | 0.42 |
| elderly | 1 | 75 | 0.560 (0.447-0.667) | 1.0000 | 0.00 |

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

### MONITOR thresholds re-selected from out-of-fold predictions (alert budget)

Budgets: vitals <= 1 alert per patient-week; behaviour Monitor days <= 10% of patient-days; agitation_prob is the only behaviour trigger (drift is report evidence only). Score scale: calibrated; the previous column is the earlier run on the raw score scale.

|  | Previous run (raw scale) | Current |
|---|---|---|
| vitals_prob | 0.443 | 0.278 |
| agitation_prob | 0.592 | 0.129 |
| Vitals alerts / patient-week | 1.00 | 1.00 |
| Vitals recall (BP/pulse/water labels) | 0.724 | 0.721 |
| Behaviour Monitor days, share of patient-days | 9.9% | 9.9% |
| Agitation recall of behaviour Monitor | 0.527 | 0.509 |

### Fault injection (3 patients x 15 days; clean run 181.6 s)

| Fault | Lost alerts | Extra alerts | Duplicates | Falls alerted | Detect (s) | Recover (s) | Cases identical to clean run | Differ only in quality flag | Differ otherwise | Pass-through after kill (s) | Agent 1-2 outputs without Agent 0 | Report modes | Wall overhead (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| kill physiological | 0 | 0 | 0 | 9/9 | 2.9 | 4.8 | 45/45 | 0 | 0 | - | 0 | llm:42,template:9 | 2.2 |
| kill behaviour | 0 | 0 | 0 | 9/9 | 2.8 | 4.0 | 45/45 | 0 | 0 | - | 0 | llm:42,template:9 | 10.7 |
| kill fall | 0 | 0 | 0 | 9/9 | 2.8 | 3.8 | 45/45 | 0 | 0 | - | 0 | template:9,llm:42 | 8.6 |
| real Gemini client, 1s timeout | 0 | 0 | 0 | 9/9 | - | - | 45/45 | 0 | 0 | - | 0 | template:47,llm:4 | -10.6 |
| LLM key removed | 0 | 0 | 0 | 9/9 | - | - | 45/45 | 0 | 0 | - | 0 | template:51 | -168.8 |
