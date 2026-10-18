# To do (agreed ideas, not built yet)

Done recently: Team Planner / Triage stage 2 (`echos/team/planner.py`, equations in `docs/TEAM_DESIGN.md`), vital-specific case files and reports, planner panel in the web app.

## 1. Domain packs and a common `Finding` (design agreed in principle, nothing built)
Today the code that turns a detector output into a finding is written three times over for vitals, behaviour and falls (triage, facts, report wording, specifics). A `Finding` is one small record every detector would emit instead:
`{domain, type, severity, urgency, evidence, specifics}`; a *domain pack* is one folder that declares a domain's data streams, detector, finding types, thresholds, critical rules, guideline tags and wording. The agents (Triage stage 2, Investigator, Coordinator, Report) would then work on Findings and never on vitals or falls directly, so a new domain plugs in without editing the agents.
- [ ] Phase 1: define `Finding` + the domain interface, wrap vitals, behaviour and fall behind it with NO behaviour change. Test: identical triage output and alerts on the 1266 replayed days (parity), then commit.
- [ ] Phase 2: show it with ONE extra domain built on data we already hold (candidate: sleep, 17 patients in `Sleep.csv`, wide uncertainty), with its own thresholds and its own limitations paragraph.
- [ ] Replay: label injected falls as a separate synthetic scenario feed, never mixed into patient-day features (live mode already has its own accelerometer channel).
- Claim only "extensible, shown with N domains"; no claim of general healthcare coverage.

## 2. Efficiency (no new agent)
- [ ] Cascade detectors: cheap z-score screen first, XGBoost + SHAP only above a low threshold; report recall lost.
- [ ] SHAP only for flagged days; incremental personal baselines (O(1) update instead of a 28-day re-read).
- [ ] Cache guidance retrieval per finding type, not per day; batch weekly work.
- [ ] Live cost terms in the Bayes gate (LLM quota left, queue depth) instead of constants.
- [ ] Later, only if several patients or domains are served: a Resource Governor agent (defers optional work, never alerts) and a Feedback/Calibration agent (proposes threshold changes for clinician approval; acknowledgements are not ground truth).

## 3. Routing follow-ups
- [ ] Near-miss case files, so a Normal day that gates the Investigator in produces something (today it is a no-op).
- [ ] Playwright test for the "Who worked on this day" card.

## 4. Needs you
- [ ] `scripts/live_llm_check.py --yes` once (real Gemini, <= 6 requests) to see whether the stricter prompt (`daily-v6`) is followed and how often the validator falls back to the template.
- [ ] Replay a few days on the dev copy so the new case files, report wording and plans exist in the UI.
- [ ] Your uncommitted `reports/metrics/agent2.json` no longer has `within_patient_audit`, so `check_docs_numbers.py` and `test_docs_numbers` fail; restore or regenerate it before committing.
