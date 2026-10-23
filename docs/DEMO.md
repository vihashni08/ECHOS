# Demo script (about 10 minutes)

## Before the demo (once, the day before)
1. Put `ECHOS_LLM_API_KEY` in `.env` (Gemini). Without it the Evidence agent writes fixed-template cards and everything else works.
2. Build the guideline index and the demo data:
   ```bash
   PYTHONPATH=. .venv/bin/python scripts/build_knowledge.py
   ```
   ```bash
   PYTHONPATH=. .venv/bin/python scripts/evaluation/demo_data.py
   ```
3. **Warm the cache (again after any change to the Evidence prompt, routing or index: the cache is keyed by the exact prompt):** run `scripts/start.sh`, open **Demo tools**, choose 20 events/s and press **Play**, and wait about 10 minutes (the Gemini free tier is rate-limited). Gemini's answers are stored in `data/knowledge/llm_cache.sqlite`, so every later run, including resets, makes no API call and works offline.
4. Keep the Mac awake (the replay pauses when it sleeps).

## Start
```bash
scripts/start.sh
```
Open http://localhost:5173, press **Presenter** (top bar) for the step strip, then **Demo tools → Play** and let the replay finish before you begin. Mail is a dry run. Say once: "this is a historical test; the system only sees data up to each day."

## 1. The question (1 min), How it works
A small team can review only a few people a day. ECHOS says who, today, and why. Decision support, not diagnosis.

## 2. Audit first (1 min), Test it yourself → "Why no detector"
88% of days at or above 160/100 are labelled, 3.7% below. The labels are confirmed threshold alerts, so a model that "detects" them re-learns a threshold. Thresholds stay rules.

## 3. You vs ECHOS (3 min), Test it yourself → "You vs ECHOS"
Let the examiner pick four people on two or three days, reveal, and read the bars. Then the honest line: a plain BP-limit rule wins on BP/pulse events (BP labels are threshold alerts); it finds almost no agitation. ECHOS covers both, so it is a balanced policy. Over all 92 days at K = 4: BP-limit rule 0.471, ECHOS 0.452, random 0.250 events per review.

## 4. Morning briefing and Decision trace (2 min)
Briefing: a ranked card with its reasons, the cutoff, and the cited guideline passage (open it). Click **Open the decision trace**: eight steps from raw readings to the evidence card. Point at the memory card: "this is all one agent knows about a person; models trained per patient: 0."

## 5. The budget is a decision (30 s), Test it yourself → budget
Drag K, change the fairness floor.

## 6. Red flag, then crash (1.5 min)
Demo tools → **Inject a reading** → "Severe high BP 192/124" → the alert is queued about 0.2 s later and the evidence card follows. On **Under the hood** kill the Patient Monitor; about 12 s later it is back. The crash table shows identical alerts and review picks with and without the kill.

## 7. Risks and limits (1 min), Under the hood → Risks
Say them before you are asked: 41 to 56 people over 3 months, labels are confirmed alerts, replay not live, no clinical validation, a BP-limit rule matches ECHOS on total events.

## If something goes wrong
| Problem | Fix |
|---|---|
| "API not reachable" banner | Restart `scripts/start.sh` |
| Play says the store holds events | Use **Reset** in Demo tools |
| Evidence cards say "Template" | No key, no network, or the check failed: the fixed wording is the designed fallback |
| "Data file missing" on Test it yourself | Run `scripts/evaluation/demo_data.py` |
| Evidence says "no supporting source" for agitation | Only NICE NG136 and NEWS2 are indexed. Add NICE NG97 as `data/knowledge/raw/NICE-NG97.pdf` and rerun `scripts/build_knowledge.py` |
