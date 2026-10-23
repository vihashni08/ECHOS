import { CheckCircle2, ShieldAlert } from "lucide-react"
import { CardSkeleton } from "@/components/States"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useRuntime } from "@/lib/queries"

type Run = Record<string, any>
export function CrashDiff() {
  const r = useRuntime()
  if (r.isError) return null
  if (!r.data) return <CardSkeleton h="h-40" />
  const ref: Run = r.data.reference, kill: Run = r.data.kill_run, d: Run = r.data.kill_vs_reference
  const rows: [string, string | number, string | number][] = [
    ["Patient-days scored", ref.days_scored, kill.days_scored], ["Urgent alerts", ref.alerts, kill.alerts], ["Reviews assigned", ref.reviews, kill.reviews],
    ["Days allocated", ref.days_allocated, kill.days_allocated], ["Messages left unconsumed", ref.unconsumed_messages, kill.unconsumed_messages],
    ["Urgent latency, median (s)", ref.urgent_latency_s.event_to_queued_median.toFixed(2), kill.urgent_latency_s.event_to_queued_median.toFixed(2)],
    ["Urgent latency, worst (s)", ref.urgent_latency_s.event_to_queued_max.toFixed(2), kill.urgent_latency_s.event_to_queued_max.toFixed(2)],
  ]
  const ok = d.lost_alerts === 0 && d.extra_alerts === 0 && d.identical_review_picks && d.identical_case_statuses
  return (
    <Card data-testid="crash-diff"><CardHeader><CardTitle>Crash test: with and without killing the Patient Monitor</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <p>Same replay of {ref.events} events, run twice with real processes. In the second run the Patient Monitor is killed (SIGKILL) at event {kill.kill.at_event}; the supervisor noticed the missing heartbeats and restarted it after <b className="num">{kill.kill.restart_s} s</b>.</p>
        <table className="w-full max-w-xl"><thead><tr className="text-left text-xs text-muted-foreground"><th className="py-1 font-medium">Measure</th><th className="font-medium">Uninterrupted</th><th className="font-medium">Agent killed</th></tr></thead>
          <tbody>{rows.map(([n, a, b]) => <tr key={n} className="border-t"><td className="py-1">{n}</td><td className="num">{a}</td><td className="num">{b}</td></tr>)}</tbody></table>
        <p className={`inline-flex items-center gap-1.5 font-semibold ${ok ? "text-normal-fg" : "text-urgent-fg"}`}><CheckCircle2 className="size-4" aria-hidden="true" />Lost alerts: {d.lost_alerts} · extra alerts: {d.extra_alerts} · review picks identical: {d.identical_review_picks ? "yes" : "no"} · case statuses identical: {d.identical_case_statuses ? "yes" : "no"}</p>
        <p className="text-xs text-muted-foreground">The one alert that arrived while the monitor was down was late (worst case {kill.urgent_latency_s.event_to_queued_max.toFixed(1)} s) but not lost. Measured on one machine with a dry-run mailer; real SMTP was never exercised.</p>
      </CardContent></Card>)
}

const RISKS: [string, string][] = [
  ["Circular labels", "Labels are confirmed threshold alerts. Mitigation: the audit; thresholds stay rules; no same-day detection claim."],
  ["Alert fatigue", "Two tiers: only NEWS2 red scores and BP ≥ 180/120 send email (0.18 per patient-week). TIHM's wider limits are attention notes."],
  ["Sensor failure looks like a quiet home", "Raw counts cannot tell them apart. A rule flags a window with zero events when the person's same-slot usual is at least 20, and vitals missing for more than 7 days, as a probable device problem. It only adds a note and never changes a score; it cannot tell a dead sensor from a person who is away. On real TIHM data it flags 7.4% of windows (0.54 probable device-problem days per patient-week), and sensitivity was only tested on a synthetic outage."],
  ["Wrong or invented explanation", "Gemini sees only structured reasons and retrieved passages, must cite them, and a non-LLM validator checks numbers, citations and wording. Failure falls back to a fixed template. It is never on the alert path."],
  ["Privacy", "De-identified ids and age bands; only reason text and guideline passages go to the language model, never an identifier. The key stays in .env."],
  ["Automation bias", "Decision support only: the clinician decides, and every card shows its evidence and its uncertainty."],
  ["Small cohort, no clinical validation", "41 to 56 people over 3 months, wide intervals, replay not live. A prospective study with clinicians is needed before any clinical claim."],
  ["A plain BP-limit rule matches ECHOS on total events", "True (0.471 vs 0.452 per review). ECHOS is a balanced policy: it also follows up 61% of agitation episodes against 8%."],
  ["Engineering limits", "One machine, SQLite bus, supervisor is a single point of restart; email delivery was only tested with a fake and a dry run."],
]
export function Risks() {
  return (
    <Card id="risks" data-testid="risks"><CardHeader><CardTitle>Risks and limits</CardTitle></CardHeader>
      <CardContent><ul className="grid gap-2 md:grid-cols-2">{RISKS.map(([t, d]) => (
        <li key={t} className="flex gap-2 rounded-lg border p-3 text-sm"><ShieldAlert className="mt-0.5 size-4 shrink-0 text-monitor-fg" aria-hidden="true" /><div><div className="font-semibold">{t}</div><p className="text-muted-foreground">{d}</p></div></li>))}</ul></CardContent></Card>)
}
