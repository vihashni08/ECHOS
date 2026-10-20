### Vitals sub-model, out-of-fold, patient-level GroupKFold. 1733 reading-days, 54 patients, prevalence 0.182

| Method | PR-AUC (95% CI) | Recall (95% CI) | False alerts / patient-week | Alerts |
|---|---|---|---|---|
| Fixed rule (sys>=140 / dia>=90 / sys<100) | 0.261 (0.183-0.346) | 0.759 (0.617-0.872) | 1.58 | 834 |
| XGBoost @ same alert count as rule | 0.697 (0.571-0.782) | 0.886 (0.827-0.938) | 1.48 | 834 |
| XGBoost @ 0.5 alerts/pt-wk | 0.697 (0.571-0.782) | 0.483 (0.378-0.608) | 0.09 | 187 |
| XGBoost @ 1.0 alerts/pt-wk | 0.697 (0.571-0.782) | 0.711 (0.619-0.789) | 0.40 | 375 |
| XGBoost @ 2.0 alerts/pt-wk | 0.697 (0.571-0.782) | 0.879 (0.810-0.920) | 1.26 | 751 |

SHAP top features: sys 0.72, dia 0.62, hr 0.38, dia_base 0.33, hr_base 0.18

### Fall agent (SisFall), subject-wise out-of-fold, threshold 0.8. Overall PR-AUC 0.997, 38 subjects

| Group | Fall subjects | Fall trials | Sensitivity (Wilson 95% CI) | Specificity (windows) | False alarms / hour |
|---|---|---|---|---|---|
| adult | 23 | 1722 | 0.918 (0.904-0.930) | 0.9983 | 0.42 |
| elderly | 1 | 75 | 0.560 (0.447-0.667) | 1.0000 | 0.00 |

Wilson CIs treat trials as independent; trials are clustered within subjects, so true uncertainty is wider (elderly = one subject).

### End-to-end replay (systems run, full period, injected falls)

| Measure | Value |
|---|---|
| Patients / patient-days / patient-weeks | 20 / 1265 / 180.7 |
| Injected falls | 60 |
| Fall detection rate (URGENT, fall reason) | 0.800 (0.682-0.882) (48/60) |
| Detection delay, simulated (fall -> end of day), median / max | 13.2 h / 23.8 h |
| False fall alerts per patient-week | 0.017 (3 alerts) |
| URGENT days from critical vitals (real TIHM readings) | 4 |
| All URGENT alerts per patient-week | 0.31 |
| Days Normal / Monitor / Urgent | 74.3% / 21.3% / 4.3% |
| Days with a data gap (any status) | 4.4% |
| Duplicate alerts | 0 |

MONITOR days by reason (270 days)

| Reason | Days | Share of MONITOR days |
|---|---|---|
| vitals only | 199 | 73.7% |
| data gap only | 47 | 17.4% |
| possible fall (no inactivity) only | 16 | 5.9% |
| data gap + vitals | 4 | 1.5% |
| possible fall (no inactivity) + vitals | 2 | 0.7% |
| data gap + possible fall (no inactivity) | 2 | 0.7% |

DATA_GAP causes, all statuses (a day can have several)

| Cause | Days |
|---|---|
| sensor gap | 55 |
| vitals gap > 7 days | 2 |

### Vitals MONITOR threshold re-selected from out-of-fold predictions (alert budget)

Budget: vitals <= 1 alert per patient-week. Score scale: calibrated.

|  | Previous (config) | Current |
|---|---|---|
| vitals_prob | 0.278 | 0.278 |
| Vitals alerts / patient-week | 1.00 | 1.00 |
| Vitals recall (BP/pulse/water labels) | 0.721 | 0.721 |
