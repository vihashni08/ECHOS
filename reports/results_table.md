### Agent 1 (vitals), out-of-fold, patient-level GroupKFold. 1733 reading-days, 54 patients, prevalence 0.182

| Method | PR-AUC (95% CI) | Recall (95% CI) | False alerts / patient-week | Alerts |
|---|---|---|---|---|
| Fixed rule (sys>=140 / dia>=90 / sys<100) | 0.261 (0.183-0.346) | 0.759 (0.617-0.872) | 1.58 | 834 |
| XGBoost @ same alert count as rule | 0.689 (0.569-0.775) | 0.883 (0.811-0.932) | 1.48 | 834 |
| XGBoost @ 0.5 alerts/pt-wk | 0.689 (0.569-0.775) | 0.479 (0.370-0.599) | 0.10 | 187 |
| XGBoost @ 1.0 alerts/pt-wk | 0.689 (0.569-0.775) | 0.702 (0.619-0.798) | 0.41 | 375 |
| XGBoost @ 2.0 alerts/pt-wk | 0.689 (0.569-0.775) | 0.876 (0.800-0.913) | 1.26 | 751 |

SHAP top features: sys 0.67, dia 0.64, hr 0.37, dia_base 0.34, hr_base 0.19

### Agent 2 (behaviour / agitation) vs base rate, out-of-fold; CI = patient bootstrap. 2308 days, 50 patients (27 with an agitation day)

| Metric | Value (95% CI) | Reference |
|---|---|---|
| PR-AUC (full agent) | 0.202 (0.137-0.301) | 4.2x base rate |
| PR-AUC, base rate (no skill) | 0.048 (0.028-0.077) | 1.0x |
| PR-AUC, personal-baseline drift only | 0.043 (0.025-0.068) | 0.9x |
| ROC-AUC (full agent) | 0.874 (0.832-0.914) | chance = 0.5 |
| ROC-AUC, drift only | 0.452 (0.384-0.529) | chance = 0.5 |
| Recall @ 0.5 alerts/pt-wk | 0.309 (0.204-0.528) | 0.39 false alerts/pt-wk |
| Recall @ 1.0 alerts/pt-wk | 0.645 (0.480-0.776) | 0.78 false alerts/pt-wk |
| Recall @ 2.0 alerts/pt-wk | 0.855 (0.754-0.961) | 1.71 false alerts/pt-wk |

Ablation: most signal is raw same-day activity level (agitated days mean 574 events vs 372); personal-baseline drift adds little. Drift-only score is at chance (ROC 0.45).

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
| Pipeline latency after last agent output, median | 0.19 s |
| False fall alerts per patient-week | 0.066 (12 alerts) |
| URGENT days from critical vitals (real TIHM readings) | 4 |
| All URGENT alerts per patient-week | 0.404 |
| Days Normal / Monitor / Urgent | 42.3% / 52.0% / 5.8% |
| Days with a data gap | 3.0% |
| Duplicate alerts | 0 |

### Fault injection (3 patients x 15 days; clean run 13.0 s)

| Fault | Lost alerts | Extra alerts | Duplicates | Falls alerted | Detect (s) | Recover (s) | Cases identical to clean run | Report modes | Wall overhead (s) |
|---|---|---|---|---|---|---|---|---|---|
| kill physiological | 0 | 0 | 0 | 9/9 | 2.9 | 4.8 | 45/45 | template:51 | -0.1 |
| kill behaviour | 0 | 0 | 0 | 9/9 | 3.1 | 4.2 | 45/45 | template:51 | 0.1 |
| kill fall | 0 | 0 | 0 | 9/9 | 2.8 | 3.9 | 45/45 | template:51 | 3.7 |
| LLM delayed 1s then timeout | 0 | 0 | 0 | 9/9 | - | - | 45/45 | template:51 | 94.9 |
| LLM key removed | 0 | 0 | 0 | 9/9 | - | - | 45/45 | template:51 | -0.2 |
