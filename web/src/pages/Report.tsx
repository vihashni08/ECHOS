import { useEffect } from "react"
import { Printer } from "lucide-react"
import { Link, useLocation } from "react-router-dom"
import { ErrorNote } from "@/components/States"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { AG_MAIN, VI_MAIN, ciTxt, f2, pc, useResults } from "@/lib/metrics"
import { useLimitations, useMetrics } from "@/lib/queries"
import { Cite, REFS } from "@/lib/refs"

/** A displayed equation (plain HTML math, no extra library). */
const Eq = ({ children }: { children: React.ReactNode }) => (
  <div className="my-2 overflow-x-auto rounded-md border bg-secondary/50 px-3 py-2 text-center font-serif text-base italic">{children}</div>
)

function H({ id, children }: { id: string; children: React.ReactNode }) {
  return <h2 id={id} className="scroll-mt-4 text-xl font-semibold">{children}</h2>
}

export default function Report() {
  const { hash } = useLocation()
  const lim = useLimitations()
  const { results: r } = useResults()
  useEffect(() => {            // react-router does not scroll to #ref-n by itself
    if (!hash) return
    const t = setTimeout(() => document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "smooth", block: "center" }), 50)
    return () => clearTimeout(t)
  }, [hash])
  const ev = (useMetrics().data?.files as Record<string, unknown> | undefined)?.v2_evidence as { gold_cases?: number; retrieval?: { routed?: { hit_at_3?: number }; naive?: { hit_at_3?: number } }; generation?: { g2_shipped?: { gemini_text_shown?: number } } } | undefined
  const ag = r.agitation?.results[AG_MAIN], vi = r.vitals?.results[VI_MAIN], al = r.allocation?.by_k["4"]
  return (
    <article className="flex max-w-4xl flex-col gap-6 text-[0.95rem] leading-relaxed">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Report & references</h1>
          <p className="text-sm text-muted-foreground">ECHOS: Evidence-Centred Health Observation and Support through Collaborative AI Agents. Final-year project, Department of IT, SSN College of Engineering.</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => window.print()} className="print:hidden"><Printer className="size-4" aria-hidden="true" />Print / save as PDF</Button>
      </div>
      <nav aria-label="Report sections" className="flex flex-wrap gap-2 text-sm print:hidden">
        {[["abstract", "Abstract"], ["data", "Data"], ["method", "Method"], ["agents", "Agents"], ["evaluation", "Evaluation"], ["findings", "Findings"], ["limitations", "Limitations"], ["references", "References"]].map(([id, t]) =>
          <a key={id} href={`#${id}`} className="rounded-md border px-2.5 py-1 font-medium hover:bg-secondary">{t}</a>)}
      </nav>

      <Card id="abstract" className="scroll-mt-4"><CardHeader><CardTitle>Abstract</CardTitle></CardHeader><CardContent className="flex flex-col gap-2">
        <p>Remote monitoring of people living with dementia produces more readings and sensor events than a small clinical team can review. We present ECHOS, a multi-agent
          decision-support system that (i) learns a personal baseline for each person's vital signs and daily activity, (ii) estimates each person's risk of a confirmed
          blood-pressure / pulse event and of agitation, (iii) allocates a fixed daily review budget across people by lift over each risk's base rate, and (iv) sends
          rule-based red-flag alerts immediately, and (v) explains every alert and review with a cited guideline passage. Four agents (Patient Monitor, Review Coordinator, Care agent, Evidence agent) run as separate processes and communicate only through a message bus.</p>
        <p>On the TIHM dataset<Cite n={1} />, evaluated on unseen patients, the agitation detector reaches PR-AUC <span className="num">{f2(ag?.pr_auc)}</span> (95% CI <span className="num">{ciTxt(ag?.ci95?.pr_auc)}</span>)
          against <span className="num">{f2(ag?.prevalence, 3)}</span> by chance; the vitals review priority reaches <span className="num">{f2(vi?.pr_auc)}</span> (<span className="num">{ciTxt(vi?.ci95?.pr_auc)}</span>) against <span className="num">{f2(vi?.prevalence)}</span>,
          and the coordinator finds <span className="num">{f2(al?.echos?.events_per_review)}</span> confirmed events per review against <span className="num">{f2(al?.random?.events_per_review)}</span> at random. A label audit shows that same-day vitals
          labels follow a threshold, so ECHOS makes no same-day detection or forecasting claim. It is decision support, not a diagnostic tool.</p>
      </CardContent></Card>

      <section className="flex flex-col gap-2">
        <H id="data">1. Data</H>
        <p>TIHM (Technology Integrated Health Management)<Cite n={1} />: {r.agitation?.data.patients ?? 56} people living with dementia monitored at home, 1 April – 30 June 2019.
          We use systolic / diastolic blood pressure and heart rate ({r.vitals?.cohort.patients ?? 41} people with at least 14 BP days), body temperature and body water (alert rules only), and
          {" "}{r.agitation?.data.activity_events.toLocaleString() ?? "1,030,559"} room and door sensor events summarised into {r.agitation?.data.windows.toLocaleString() ?? "8,516"} activity windows.
          Labels are alerts that the study's monitoring team confirmed: {r.vitals?.cohort.event_days ?? 280} BP / pulse event days and {r.agitation?.data.positives ?? 135} agitation episodes.
          Not used: sleep (only 17 people), skin temperature, weight / temperature / body-water labels (too few), falls (TIHM has none).</p>
      </section>

      <section className="flex flex-col gap-2">
        <H id="method">2. Method</H>
        <h3 className="font-semibold">2.1 Personal baseline (each vital, each person)</h3>
        <p>Readings are modelled as <i>x</i> ~ N(μ, σ²) with a conjugate Normal-Inverse-Gamma prior<Cite n={4} /> fitted on training patients by moment matching. After <i>n</i> past readings:</p>
        <Eq>κ<sub>n</sub> = κ<sub>0</sub> + n, &nbsp; μ<sub>n</sub> = (κ<sub>0</sub>μ<sub>0</sub> + n x̄) / κ<sub>n</sub>, &nbsp; α<sub>n</sub> = α<sub>0</sub> + n/2, &nbsp;
          β<sub>n</sub> = β<sub>0</sub> + ½Σ(x<sub>i</sub> − x̄)² + κ<sub>0</sub>n(x̄ − μ<sub>0</sub>)² / 2κ<sub>n</sub></Eq>
        <p>The person's <span className="font-medium">usual</span> is μ<sub>n</sub>; each new reading gets z = (x − μ<sub>n</sub>) / scale from the Student-t predictive. Only past readings are used, so a new person starts at the population average and moves to their own level.</p>
        <h3 className="mt-2 font-semibold">2.2 Personal event rate and 3-day risk (BP / pulse review priority)</h3>
        <p>Each person's daily rate of confirmed events r<sub>i</sub> ~ Beta(a<sub>0</sub>, b<sub>0</sub>), with an empirical-Bayes prior from training patients<Cite n={4} />. After k<sub>t</sub> event days in n<sub>t</sub> days:</p>
        <Eq>r̂<sub>t</sub> = (a<sub>0</sub> + k<sub>t</sub>) / (a<sub>0</sub> + b<sub>0</sub> + n<sub>t</sub>), &nbsp;&nbsp; P(event in next 3 days) = 1 − (1 − r̂<sub>t</sub>)³</Eq>
        <p>with a 95% credible interval from Beta(a<sub>0</sub> + k<sub>t</sub>, b<sub>0</sub> + n<sub>t</sub> − k<sub>t</sub>). The estimate updates after every confirmed outcome (the feedback loop).</p>
        <h3 className="mt-2 font-semibold">2.3 Agitation detector</h3>
        <p>For each window (night 18–08, morning 08–12, afternoon 12–18): counts per room and door, events per hour, room-to-room transitions, longest quiet gap, short gaps, active hours,
          room entropy; each also as a personal same-slot score z = (x − median<sub>prev 14</sub>) / (IQR<sub>prev 14</sub> + 1); plus episode history strictly before the window.
          Classifier: XGBoost<Cite n={7} />, calibrated with Platt scaling<Cite n={8} /> fitted on training patients' out-of-fold scores.</p>
        <h3 className="mt-2 font-semibold">2.4 Review allocation</h3>
        <Eq>priority<sub>i</sub> = max( v<sub>i</sub> / v̄ , a<sub>i</sub> / ā )</Eq>
        <p>v<sub>i</sub>, a<sub>i</sub>: the person's BP / pulse and agitation risks; v̄, ā: their base rates. The K = 4 highest are reviewed; a fairness floor lets anyone unseen for 7 days take up to half the slots.
          A restless-bandit index and a two-state hidden Markov model were tested as alternatives and kept only as ablations.</p>
        <h3 className="mt-2 font-semibold">2.5 Evidence: cited guideline passages</h3>
        <p>The guidelines (NEWS2<Cite n={2} />, NICE NG136<Cite n={3} />, NG97<Cite n={5} />, CG103<Cite n={6} />) are split into passages and indexed with TF-IDF<Cite n={9} />. Each reason is routed to
          a query; the best passages are retrieved and a language model (Gemini) writes 2–3 sentences that may use only those passages and must cite them<Cite n={10} />. A validator rejects
          any text with a citation not retrieved, a number not in the facts, or banned wording; then a fixed template is used instead. With no network the agent uses its cache or the template, so the demo never depends on the internet.</p>
        <h3 className="mt-2 font-semibold">2.6 Urgent alerts (no model)</h3>
        <p>URGENT = a NEWS2 single-parameter red score<Cite n={2} /> (systolic ≤ 90 mmHg, pulse ≤ 40 or ≥ 131 bpm, temperature ≤ 35.0 or ≥ 39.1 °C) or severe hypertension, BP ≥ 180/120<Cite n={3} />.
          Sent at once to the caregiver and clinician from a fixed template with a de-identified id; escalated to a second contact if not acknowledged.</p>
      </section>

      <section className="flex flex-col gap-2">
        <H id="agents">3. Agents</H>
        <p>Four agents in the sense of Wooldridge and Jennings<Cite n={11} />: each has its own goal, persistent memory and decisions, and acts only by sending messages.</p>
        <ul className="list-disc pl-5">
          <li><span className="font-semibold">Patient Monitor</span> (one per person): scores each day, raises URGENT, sends a review request with reasons; learns from feedback before scoring the next day.</li>
          <li><span className="font-semibold">Review Coordinator</span>: at day close, waits for every request, allocates K reviews, writes the review list and case statuses (never downgrades URGENT).</li>
          <li><span className="font-semibold">Care agent</span>: delivers alerts with retries and escalation, opens review tasks, passes outcomes back as feedback.</li>
          <li><span className="font-semibold">Evidence agent</span>: after an alert is queued or a review assigned, retrieves guideline passages and writes a short cited explanation. It never changes a score, a pick or an alert, and never delays an urgent alert.</li>
        </ul>
        <p>A supervisor restarts any agent whose heartbeat stops; each agent resumes from its checkpoint, and all work is idempotent. See <Link to="/system" className="underline">System</Link>.</p>
      </section>

      <section className="flex flex-col gap-2">
        <H id="evaluation">4. Evaluation protocol</H>
        <ul className="list-disc pl-5">
          <li>5-fold cross-validation split <span className="font-medium">by patient</span>; every prior, model and calibration fitted on training patients only; every score out-of-fold.</li>
          <li>Primary metric PR-AUC<Cite n={12} />; recall and precision at a fixed budget of 1 alert per patient-week; false alerts per patient-week. Not accuracy.</li>
          <li>95% confidence intervals from 1000 patient-level bootstrap samples<Cite n={13} />; paired bootstrap for differences, "better" only if the whole interval is above zero.</li>
          <li>The live demo scores each person with the fold model that excluded them: no in-sample demo.</li>
        </ul>
      </section>

      <section className="flex flex-col gap-2">
        <H id="findings">5. Findings</H>
        <ul className="list-disc pl-5">
          <li>Agitation: PR-AUC {f2(ag?.pr_auc)} vs {f2(ag?.prevalence, 3)} chance; {ag?.at_budget?.caught} of {ag?.at_budget?.positives} episodes caught at 1 alert / patient-week (precision {pc(ag?.at_budget?.precision)}). The personal baseline adds a small but real gain over raw counts.</li>
          <li>BP / pulse priority: PR-AUC {f2(vi?.pr_auc)} vs {f2(vi?.prevalence)} chance; better than the study's threshold rule; ties XGBoost; most useful for new patients.</li>
          <li>Review allocation: {f2(al?.echos?.events_per_review)} vs {f2(al?.random?.events_per_review)} confirmed events per review; the fairness floor cuts never-reviewed people from {al?.echos?.patients_never_reviewed} to {al?.["echos+fairness7"]?.patients_never_reviewed}.</li>
          <li>Evidence: on {ev?.gold_cases ?? 37} hand-labelled cases the right guideline passage is in the top 3 for {pc(ev?.retrieval?.routed?.hit_at_3)} (plain search: {pc(ev?.retrieval?.naive?.hit_at_3)});
            {" "}{pc(ev?.generation?.g2_shipped?.gemini_text_shown)} of generated texts pass the validator (the rest use the template), and none of the checked texts made an unsupported claim.</li>
          <li>Runtime: killing an agent mid-run lost {r.runtime?.kill_vs_reference.lost_alerts ?? 0} alerts and changed no review decision.</li>
        </ul>
        <p className="text-sm text-muted-foreground">Every number comes from reports/metrics/v2_*.json; the full tables and comparisons are in docs/METHOD.md.</p>
      </section>

      <section className="flex flex-col gap-2">
        <H id="limitations">6. Limitations</H>
        {lim.isError ? <ErrorNote error={lim.error} /> : <div className="whitespace-pre-line" data-testid="limitations">{lim.data?.text.replace(/\*\*|`/g, "")}</div>}
        <h3 className="mt-2 font-semibold">Future work</h3>
        <p>Evaluate with clinicians answering review tasks (the feedback path exists: POST /api/v2/reviews/outcome); a larger and longer cohort; hospital datasets (e.g. MIMIC-IV) for the vitals models.</p>
      </section>

      <section className="flex flex-col gap-2">
        <H id="references">References</H>
        <ol className="flex flex-col gap-2" data-testid="references">
          {REFS.map((x) => (
            <li key={x.n} id={`ref-${x.n}`} className="scroll-mt-4 rounded-md p-1 target:bg-accent">
              <div><span className="num mr-1 font-semibold">[{x.n}]</span>{x.text}{x.url && <> <a href={x.url} target="_blank" rel="noreferrer" className="break-all text-primary underline">{x.url.replace("https://", "")}</a></>}</div>
              <div className="pl-7 text-xs text-muted-foreground">Used for: {x.used}</div>
            </li>))}
        </ol>
        <p className="text-xs text-muted-foreground">TIHM data courtesy of the Surrey and Borders Partnership NHS Foundation Trust. Patient-level data are not published; only aggregates appear here.</p>
      </section>
    </article>
  )
}
