import { Navigate, useNavigate, useParams } from "react-router-dom"
import TrendChart, { type Row } from "@/charts/TrendChart"
import { CardSkeleton, Empty, ErrorNote } from "@/components/States"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { pct, personLabel } from "@/lib/live"
import { useMessages, useMonitor, usePatients } from "@/lib/queries"
import { StatusBadge } from "@/lib/status"
import { NAME } from "@/components/AgentStrip"

export function PatientRedirect() {
  const p = usePatients()
  const first = p.data?.items[0]?.patient_id
  if (!p.data) return <CardSkeleton />
  return first ? <Navigate to={`/patients/${first}`} replace /> : <Empty title="No patients yet">Start the replay from Demo tools.</Empty>
}

type V = { value?: number | null; usual?: number | null; z?: number | null }
const vit = (d: { vitals: Record<string, unknown> }, k: string) => (d.vitals?.[k] ?? {}) as V
const TOPIC: Record<string, string> = { urgent: "URGENT", review_request: "review request", feedback: "feedback", assign: "assigned for review", review_decision: "review decision",
  urgent_sent: "alert queued", alert_escalated: "alert escalated", clinician_outcome: "clinician outcome" }

export default function PatientPage() {
  const { id = "" } = useParams()
  const nav = useNavigate()
  const ps = usePatients()
  const m = useMonitor(id, 60)
  const msgs = useMessages(40, id)
  const days = [...(m.data ?? [])].reverse()            // oldest first for the charts
  const rows: Row[] = days.map((d) => ({
    day: d.day, sys: vit(d, "sys").value, sys_u: vit(d, "sys").usual, dia: vit(d, "dia").value, dia_u: vit(d, "dia").usual,
    hr: vit(d, "hr").value, hr_u: vit(d, "hr").usual,
    flag_bp: d.urgent.some((u) => /systolic|diastolic/.test(u)) ? vit(d, "sys").value : null,       // marker on the reading that crossed a red flag
    flag_hr: d.urgent.some((u) => /pulse/.test(u)) ? vit(d, "hr").value : null,
    agit: d.agitation_max, risk: d.vitals_risk,
  }))
  const latest = m.data?.[0]
  const n = (k: string) => rows.filter((r) => r[k] != null).length
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="num text-2xl font-semibold">{personLabel(id)}</h1>
          <p className="text-sm text-muted-foreground">What this person's Patient Monitor saw and concluded, day by day. Every reading is compared with their own usual (past readings only).</p>
        </div>
        <Select value={id} onValueChange={(v) => nav(`/patients/${v}`)}>
          <SelectTrigger className="w-44" aria-label="Choose a patient"><SelectValue /></SelectTrigger>
          <SelectContent>{(ps.data?.items ?? []).map((p) => <SelectItem key={p.patient_id} value={p.patient_id}>{personLabel(p.patient_id)}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      {m.isError && <ErrorNote error={m.error} />}
      {!m.data ? <CardSkeleton /> : m.data.length === 0 ? <Empty title="Nothing scored yet">This person's days appear as the replay reaches them.</Empty> : (<>
        <section aria-label="Latest day" className="grid gap-3 md:grid-cols-3" data-testid="belief">
          <div className="rounded-xl border bg-card p-3"><div className="text-xs text-muted-foreground">Latest day</div><div className="num text-lg font-semibold">{latest?.day}</div><StatusBadge status={latest?.status} /></div>
          <div className="rounded-xl border bg-card p-3"><div className="text-xs text-muted-foreground">Personal rate of confirmed BP / pulse events</div>
            <div className="num text-lg font-semibold">{pct(latest?.rate)} <span className="text-sm font-normal text-muted-foreground">per day</span></div>
            <div className="num text-xs text-muted-foreground">95% credible interval {latest?.rate_ci95 ? `${pct(latest.rate_ci95[0])}–${pct(latest.rate_ci95[1])}` : "n/a"}</div></div>
          <div className="rounded-xl border bg-card p-3"><div className="text-xs text-muted-foreground">Risks sent to the Review Coordinator</div>
            <div className="num text-sm">BP / pulse event, next 3 days: <span className="font-semibold">{pct(latest?.vitals_risk)}</span></div>
            <div className="num text-sm">Agitation, highest window today: <span className="font-semibold">{pct(latest?.agitation_max, 1)}</span></div></div>
        </section>
        <div className="grid gap-4 lg:grid-cols-2">
          <TrendChart title="Blood pressure vs this person's usual" unit="mmHg" rows={rows} flagKey="flag_bp"
            series={[{ key: "sys", name: "Systolic", color: "var(--chart-1)" }, { key: "sys_u", name: "Usual systolic", color: "var(--chart-1)", dashed: true },
              { key: "dia", name: "Diastolic", color: "var(--chart-2)" }, { key: "dia_u", name: "Usual diastolic", color: "var(--chart-2)", dashed: true }]}
            summary={`${n("sys")} days with a blood-pressure reading; dashed lines are the personal usual built from earlier readings only.`} />
          <TrendChart title="Pulse vs this person's usual" unit="bpm" rows={rows} flagKey="flag_hr"
            series={[{ key: "hr", name: "Pulse", color: "var(--chart-1)" }, { key: "hr_u", name: "Usual pulse", color: "var(--chart-1)", dashed: true }]}
            summary={`${n("hr")} days with a pulse reading.`} />
          <TrendChart title="Agitation probability (highest window of the day)" unit="" rows={rows} fmt={(v) => `${Math.round(v * 100)}%`} domain={[0, "auto"]}
            series={[{ key: "agit", name: "Agitation probability", color: "var(--chart-1)", kind: "bar" }]}
            summary={`${n("agit")} days with activity-sensor windows. Calibrated probability from room and door sensors against this person's usual for that time of day.`} />
          <TrendChart title="Risk of a BP / pulse event in the next 3 days" unit="" rows={rows} fmt={(v) => `${Math.round(v * 100)}%`} domain={[0, 1]}
            refLine={{ y: 0.304, label: "base rate 30%" }} series={[{ key: "risk", name: "3-day risk", color: "var(--chart-1)" }]}
            summary="From the person's own confirmed events (Bayesian rate shrunk towards the population); it updates after each day's feedback." />
        </div>
        <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <Card><CardHeader><CardTitle>Day by day</CardTitle></CardHeader><CardContent>
            <ul className="flex flex-col gap-2" data-testid="day-log">{(m.data ?? []).map((d) => (
              <li key={d.day} className="rounded-lg border p-2.5 text-sm">
                <div className="flex flex-wrap items-center gap-2"><span className="num font-semibold">{d.day}</span><StatusBadge status={d.status} className="text-xs" />
                  {d.reviewed && <span className="text-xs font-semibold">review #{d.reviewed}</span>}</div>
                {(d.urgent.length > 0 || d.reasons.length > 0) && <ul className="mt-1 list-disc pl-5">{[...d.urgent.map((u) => `URGENT: ${u}`), ...d.reasons.filter((r) => !r.startsWith("URGENT"))].map((r, k) => <li key={k}>{r}</li>)}</ul>}
              </li>))}</ul>
          </CardContent></Card>
          <Card><CardHeader><CardTitle>Agent messages about this person</CardTitle></CardHeader><CardContent>
            {!msgs.data?.length ? <p className="text-sm text-muted-foreground">No messages yet.</p> : (
              <ol className="flex flex-col gap-1.5 text-sm" data-testid="patient-messages">{msgs.data.map((x) => (
                <li key={x.id} className="rounded-md border px-2.5 py-1.5"><span className="font-medium">{NAME[x.sender] ?? x.sender}</span> → <span className="font-medium">{NAME[x.recipient] ?? x.recipient}</span>: {TOPIC[x.topic] ?? x.topic} <span className="num text-xs text-muted-foreground">{x.day}</span></li>))}</ol>)}
          </CardContent></Card>
        </div>
      </>)}
    </div>
  )
}
