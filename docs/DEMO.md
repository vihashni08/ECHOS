# Demo script (about 8 minutes)

## Before the panel
```bash
scripts/start.sh
```
It starts the API, the four agents and the web app, replays the first 21 of the 28 demo days quickly and **pauses** (about 30 s).
Open http://localhost:5173. The top bar should say **REPLAY · paused 357/470**, **Live** and **4/4 agents running**. Mail is a dry run.
For a clean run again: Ctrl-C, then `scripts/start.sh` again.

The menu has four items: **Daily review** (the demo), **Patients**, **Report & references**, **System (4 agents)**.

## 1. Daily review: the core of ECHOS (4 min)
Say: "A small team can review four people a day. This is one real day from the TIHM study for our 8 demo patients."
1. **The four steps across the top** are the four agents: Patient Monitor reads each person's day, Review Coordinator picks the top 4,
   Care agent alerts and opens tasks, Evidence agent cites the guideline.
2. **Section 1, every patient:** each BP and pulse is shown next to *that person's own usual* (▲/▼ = more than 2 of their own SDs away),
   the most unusual activity window, the two risks, and the priority: how many times above the average person.
   The top 4 rows carry **Review #1–#4**.
3. **Section 2, the 4 reviews:** read one card aloud: the reasons (e.g. "agitation risk 37% in the afternoon window: fridge door 44,
   usual-adjusted z +2.7") and the **guideline passage**. Click the passage line to show the NICE NG97 / NEWS2 text and page. The badge
   says whether Gemini wrote it (checked by the validator) or the fixed template was used. "Study record" shows what really happened.
4. **Change the day** with ◀ ▶. Go to **2019-06-08**: section 3 shows two urgent alerts (diastolic 136 → NICE NG136 p.24;
   systolic 87 → NEWS2 p.53 and p.19).

## 2. Live: the agents at work (2 min)
1. **Demo tools → Play** (5 events/s). Close the sheet. The page moves forward day by day to 16 June (about 25 s) as the agents decide.
2. **Demo tools → Inject a reading:** patient 0d5ef, "Severe high BP 192/124", **Inject**. On Daily review (16 June), the patient row turns red
   and section 3 shows the urgent alert at once; the Evidence agent's cited card follows about 10 s later (sooner from the cache, or the template offline).

## 3. System (4 agents): it is real (1 min)
Four processes, heartbeats, the message trace. **Kill agent** on the Patient Monitor: about 14 s later it is alive again (restarts: 1), nothing lost.

## 4. Report & references (1 min)
Method with the equations, how it was evaluated (split by patient, PR-AUC, bootstrap CIs), findings, limitations, and the 13 numbered references
every [n] on the pages links to. **Print / save as PDF** gives a clean copy.

## Likely questions
| Question | Answer |
|---|---|
| Why is 87 mmHg a red flag? | NEWS2 red score: systolic ≤ 90 [2]. Patient 46286 is really that low in TIHM. |
| Why these 4 and not d93d8 (92% BP risk)? | Priority is risk ÷ the average person's risk; an agitation risk 10× above average outranks a BP risk 3× above. |
| Where is the algorithm? | Report §2: personal Bayesian baseline and event rate, XGBoost + Platt for agitation, lift allocation, TF-IDF retrieval + validator for evidence. |
| Does Gemini decide anything? | No. It only words the explanation, from retrieved passages, and a validator checks it; scores, picks and alerts never use it. |
| Real agents? | Separate processes with their own memory and goals, talking only via the message bus; the kill test proves it. |

## If something goes wrong
| Problem | Fix |
|---|---|
| "API not reachable" banner | Ctrl-C, run `scripts/start.sh` again |
| Port already in use | Another copy is running: stop it |
| Evidence card says "waiting" | Give it ~10 s (Gemini); offline it uses the template |
| Running the tests during the demo | Safe now: `--check` uses its own database |
