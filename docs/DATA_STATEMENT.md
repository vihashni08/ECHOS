# Data statement

## Data sources and terms
- **TIHM** (Technology Integrated Health Management): Palermo et al., *Scientific Data* 2023, doi [10.1038/s41597-023-02519-y](https://doi.org/10.1038/s41597-023-02519-y); data at <https://zenodo.org/records/7622128>. It is provided for research and patient-benefit purposes and **cannot be used for commercial purposes**. Any publication or use must acknowledge the **Surrey and Borders Partnership NHS Foundation Trust**. The terms may change: check the Zenodo page for the current terms before using or sharing anything derived from it.
- **SisFall**: Sucerquia, López and Vargas-Bonilla, "SisFall: A Fall and Movement Dataset", *Sensors* 2017, 17(1), 198. Used only to train and evaluate the fall model (young adult and elderly volunteers; scripted falls).

## What this repository contains
- **No TIHM data.** Raw files, patient-day tables, databases, models and parquet files are git-ignored and never committed (`data/`, `*.db`, `*.joblib`, `*.parquet`).
- Derived **patient-level outputs stay local**: weekly PDFs, daily reports, rating sheets (`reports/rating/`), the end-to-end per-day file, per-patient EDA tables, live-check output (`reports/live_check/`) and UI screenshots are not published.
- Only **aggregate figures** are published (`reports/metrics/*.json`, `reports/RESULTS.md`, `reports/results_table.*`, summary plots).
- Prompts sent to the report LLM use an alias ("Patient A") instead of the TIHM id; alert e-mails carry a de-identified id and age band only.

## Labels and their limits
See [LIMITATIONS.md](LIMITATIONS.md): the dataset papers describe the labels as clinician-verified events at 6-hour resolution; we did not verify how each was produced, so vitals results are an upper bound on generalisation.

## Acknowledgement
TIHM data courtesy of the Surrey and Borders Partnership NHS Foundation Trust (Palermo et al., 2023).
