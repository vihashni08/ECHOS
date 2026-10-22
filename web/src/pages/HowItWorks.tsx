import { CardSkeleton, ErrorNote } from "@/components/States"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useComponents, useLimitations, useMetrics } from "@/lib/queries"

type J = Record<string, unknown>
const g = (o: unknown, ...path: (string | number)[]): unknown => path.reduce<unknown>((x, k) => (x && typeof x === "object" ? (x as J)[k as string] : undefined), o)
const n = (v: unknown, d = 2) => (typeof v === "number" ? v.toFixed(d) : "n/a")
const ci = (v: unknown) => (Array.isArray(v) ? `${n(v[0])}–${n(v[1])}` : "n/a")

const FLOW = [
  ["Patient Monitor", "scores the day: readings against the person's usual, agitation probability for each activity window. A red-flag reading goes straight to the Care agent as URGENT."],
  ["Care agent", "queues the URGENT alert to the caregiver and clinician at once (fixed template, no model) and escalates if nobody acknowledges it."],
  ["Care agent → Patient Monitor", "passes on the day's outcome (in the replay: what the TIHM monitoring team confirmed). The monitor updates its personal event rate and episode history."],
  ["Patient Monitor → Review Coordinator", "sends a review request: 3-day BP / pulse risk, agitation risk, and the reasons in plain words."],
  ["Review Coordinator", "when the day closes, ranks everyone by lift over each risk's base rate, keeps the fairness floor, picks K people and sends the assignments to the Care agent."],
  ["Care agent", "opens a review task per person; the clinician's answer flows back as feedback, closing the loop."],
]

export default function HowItWorks() {
  const comps = useComponents()
  const m = useMetrics()
  const lim = useLimitations()
  const f = (m.data?.files ?? {}) as J
  const ag = g(f, "v2_agitation", "results", "ECHOS agitation detector")
  const vi = g(f, "v2_vitals", "results", "ECHOS: personal Bayesian rate")
  const al = g(f, "v2_allocation", "by_k", "4")
  const rt = g(f, "v2_runtime")
  const au = g(f, "v2_label_audit")
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">How it works</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">ECHOS is decision support for home monitoring of people living with dementia (TIHM study data). It does not diagnose, and no model decides an emergency: red flags are fixed clinical rules.</p>
      </div>
      <Card><CardHeader><CardTitle>One day, step by step</CardTitle></CardHeader><CardContent>
        <ol className="flex list-decimal flex-col gap-1.5 pl-5 text-sm">{FLOW.map(([a, b], i) => <li key={i}><span className="font-semibold">{a}</span> {b}</li>)}</ol>
      </CardContent></Card>
      <Card><CardHeader><CardTitle>Results (tested on patients the models never saw)</CardTitle></CardHeader><CardContent>
        {m.isError ? <ErrorNote error={m.error} /> : !m.data ? <CardSkeleton /> : (
          <table className="w-full text-sm" data-testid="results">
            <thead><tr className="text-left text-xs text-muted-foreground"><th className="py-1 font-medium">Part</th><th className="font-medium">Result</th><th className="font-medium">Chance / comparison</th></tr></thead>
            <tbody>
              <tr className="border-t"><td className="py-1.5 pr-2 font-medium">Agitation detection</td>
                <td className="num">PR-AUC {n(g(ag, "pr_auc"))} (95% CI {ci(g(ag, "ci95", "pr_auc"))}), ROC {n(g(ag, "roc_auc"))}; caught {String(g(ag, "at_budget", "caught") ?? "n/a")} of {String(g(ag, "at_budget", "positives") ?? "n/a")} at 1 alert / patient-week</td>
                <td className="num">chance {n(g(ag, "prevalence"), 3)}</td></tr>
              <tr className="border-t"><td className="py-1.5 pr-2 font-medium">BP / pulse review priority</td>
                <td className="num">PR-AUC {n(g(vi, "pr_auc"))} (95% CI {ci(g(vi, "ci95", "pr_auc"))})</td>
                <td className="num">chance {n(g(vi, "prevalence"))}; beats the study's own threshold rule by {n(g(f, "v2_vitals", "ours_minus_baseline_pr_auc", "TIHM safety threshold today", "diff"), 3)}</td></tr>
              <tr className="border-t"><td className="py-1.5 pr-2 font-medium">Review Coordinator (4 / day)</td>
                <td className="num">{n(g(al, "echos", "events_per_review"))} confirmed events per review</td>
                <td className="num">random {n(g(al, "random", "events_per_review"))}</td></tr>
              <tr className="border-t"><td className="py-1.5 pr-2 font-medium">Urgent alert speed</td>
                <td className="num">{n(g(rt, "reference", "urgent_latency_s", "event_to_queued_median"))} s median, reading → alert queued</td>
                <td className="num">kill test: {String(g(rt, "kill_vs_reference", "lost_alerts") ?? "n/a")} lost, {String(g(rt, "kill_vs_reference", "extra_alerts") ?? "n/a")} extra alerts</td></tr>
              <tr className="border-t"><td className="py-1.5 pr-2 font-medium">Label audit</td>
                <td>{n(Number(g(au, "q1_bp_labels_vs_thresholds", 2, "share_labelled")) * 100, 0)}% of days with BP ≥ 160/100 carry a BP label</td>
                <td>so same-day "detection" would just re-learn a threshold</td></tr>
            </tbody>
          </table>)}
        <p className="mt-2 text-xs text-muted-foreground">Source: reports/metrics/v2_*.json (5-fold cross-validation split by patient, patient-level bootstrap). Full method: docs/METHOD.md.</p>
      </CardContent></Card>
      <Card><CardHeader><CardTitle>Components</CardTitle></CardHeader><CardContent>
        {!comps.data ? <CardSkeleton /> : (
          <ul className="grid gap-2 md:grid-cols-2" data-testid="components">{comps.data.map((c) => (
            <li key={c.id} className="rounded-lg border p-2.5 text-sm"><div className="flex justify-between gap-2"><span className="font-semibold">{c.name}</span><span className="text-xs uppercase text-muted-foreground">{c.kind}{c.status !== "n/a" ? ` · ${c.status}` : ""}</span></div>
              <p className="mt-1">{c.role}</p><p className="mt-1 text-xs text-muted-foreground">If it fails: {c.on_failure}</p></li>))}</ul>)}
      </CardContent></Card>
      <Card><CardHeader><CardTitle>Limitations</CardTitle></CardHeader><CardContent>
        {lim.isError ? <ErrorNote error={lim.error} /> : <div className="text-sm whitespace-pre-line" data-testid="limitations">{lim.data?.text.replace(/\*\*/g, "")}</div>}
      </CardContent></Card>
    </div>
  )
}
