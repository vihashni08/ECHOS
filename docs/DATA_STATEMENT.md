# Data statement

## Data sources and terms
- **TIHM** (Technology Integrated Health Management): Palermo et al., *Scientific Data* 2023, doi [10.1038/s41597-023-02519-y](https://doi.org/10.1038/s41597-023-02519-y); data at <https://zenodo.org/records/7622128>. It is provided for research and patient-benefit purposes and **cannot be used for commercial purposes**. Any publication or use must acknowledge the **Surrey and Borders Partnership NHS Foundation Trust**. The terms may change: check the Zenodo page for the current terms before using or sharing anything derived from it.

## What this repository contains
- **No TIHM data.** Raw files, patient-day tables, databases, models and parquet files are git-ignored and never committed (`data/`, `*.db`, `*.joblib`, `*.parquet`).
- Derived **patient-level outputs stay local**: out-of-fold scores (`data/v2/*.parquet`), demo databases and UI screenshots are not published.
- Only **aggregate figures** are published (`reports/metrics/v2_*.json`, docs/METHOD.md).
- Alert e-mails carry the TIHM study's de-identified id and an age band only.

## Labels and their limits
Labels are alerts raised by the study's own rules and model that its monitoring team confirmed (Palermo et al., 2023). Our label audit (docs/METHOD.md, section 2) shows the blood-pressure labels largely follow a same-day threshold, so ECHOS does not claim same-day vitals detection or forecasting of new episodes.

## Acknowledgement
TIHM data courtesy of the Surrey and Borders Partnership NHS Foundation Trust (Palermo et al., 2023).
