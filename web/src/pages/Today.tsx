import { Link } from "react-router-dom"
import { Activity, ArrowRight, ClipboardList, MessageSquare, Radio } from "lucide-react"
import { AlertCard, isOpen } from "@/components/AlertCard"
import { Empty, ErrorNote, CardSkeleton } from "@/components/States"
import Timeline from "@/components/Timeline"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ago, personLabel, useMode } from "@/lib/live"
import { useAlertList, useCase, useLive, useMessages, usePatients, useSummary, useTaskDone, useTasks } from "@/lib/queries"
import { fmtDay } from "@/lib/format"
import { StatusBadge } from "@/lib/status"
import type { Patient } from "@/lib/api"

function AttentionRow({ p, provisional }: { p: Patient; provisional: boolean }) {
  const sum = useSummary(p.patient_id)
  const c = useCase(p.patient_id)
  const reasons = sum.data?.reasons ?? []
  return (
    <li>
      <Link to={`/patients/${p.patient_id}`} className="block rounded-xl border p-3 transition-shadow hover:shadow-md" data-testid="attention-row" aria-label={`Open ${personLabel(p.patient_id)}`}>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div><div className="text-base font-semibold">{personLabel(p.patient_id)} <span className="text-sm font-normal text-muted-foreground">· age {p.age_band} · {p.sex}</span></div>
            <div className="num text-sm text-muted-foreground">{fmtDay(p.latest_day)}{provisional ? " · so far today (day still open)" : ""}</div></div>
          <div className="flex items-center gap-2"><StatusBadge status={p.latest_status} />
            {c.data && <span className="rounded-full border px-2 py-0.5 text-xs font-semibold" data-testid="row-evidence">{c.data.evidence_level.toLowerCase()} evidence</span>}</div>
        </div>
        <p className="mt-2 text-sm">{c.data?.headline ?? (reasons[0] ?? "Changes from this person's usual were noticed.")}</p>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2"><Timeline pid={p.patient_id} days={14} /><span className="inline-flex items-center gap-1 text-sm font-medium">Open case<ArrowRight className="size-4" aria-hidden="true" /></span></div>
      </Link>
    </li>
  )
}

function DataRow({ p }: { p: Patient }) {
  const sum = useSummary(p.patient_id)
  return (
    <li><Link to={`/patients/${p.patient_id}`} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border p-3 hover:shadow-md" aria-label={`Open ${personLabel(p.patient_id)}`}>
      <div><div className="font-semibold">{personLabel(p.patient_id)} <span className="text-sm font-normal text-muted-foreground">· {fmtDay(p.latest_day)}</span></div>
        <div className="text-sm">{sum.data?.data_note ?? sum.data?.reasons?.[0] ?? "Some data is missing."}</div></div>
      <StatusBadge status="DATA_GAP" /></Link></li>
  )
}

function Tasks() {
  const t = useTasks("OPEN")
  const done = useTaskDone()
  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><ClipboardList className="size-4" aria-hidden="true" />Open tasks</CardTitle></CardHeader>
      <CardContent>
        {!t.data?.items.length ? <p className="text-sm text-muted-foreground" data-testid="no-tasks">Nothing waiting. The Care Coordinator opens a check-in after an acknowledged fall and a review after repeated changes.</p> : (
          <ul className="flex flex-col gap-2" data-testid="tasks">
            {t.data.items.map((x) => (
              <li key={x.id} className="rounded-lg border p-2.5 text-sm"><div className="font-medium">{x.title}</div>
                <div className="text-muted-foreground"><Link to={`/patients/${x.patient_id}`} className="underline">{personLabel(x.patient_id)}</Link> · {x.kind === "CHECK_IN" ? "check-in" : "clinician review"}</div>
                <Button size="sm" variant="outline" className="mt-1.5" onClick={() => done.mutate(x.id)} disabled={done.isPending}>Mark done</Button></li>))}
          </ul>)}
      </CardContent>
    </Card>
  )
}

function Freshness() {
  const live = useLive()
  const ps = live.data?.patients ?? []
  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Radio className="size-4" aria-hidden="true" />Is the data arriving?</CardTitle></CardHeader>
      <CardContent>
        {!ps.length ? <p className="text-sm text-muted-foreground">No device has sent data yet.</p> : (
          <ul className="flex flex-col gap-2 text-sm" data-testid="freshness">
            {ps.map((p) => {
              const stale = Object.entries(p.streams ?? {}).filter(([, s]) => s.state === "stale").map(([k]) => k)
              return (
                <li key={p.patient_id} className="flex flex-wrap items-center justify-between gap-2">
                  <span className="num">{personLabel(p.patient_id)}</span>
                  <span className={stale.length ? "font-semibold text-urgent-fg" : "text-muted-foreground"}>{stale.length ? `${stale.join(", ")} silent` : `last event ${ago(Math.min(...Object.values(p.streams ?? {}).map((s) => s.last_wall_age_s ?? 1e9)))}`}</span>
                </li>)
            })}
          </ul>)}
      </CardContent>
    </Card>
  )
}

const WHO: Record<string, string> = { triage: "Triage", investigator: "Case Investigator", coordinator: "Care Coordinator", report: "Report agent", all: "everyone" }
const TOPIC: Record<string, string> = { case_opened: "opened a case for", case_file_ready: "finished the case file for", alert_escalated: "escalated an alert for" }
function Activity_() {
  const m = useMessages(8)
  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><MessageSquare className="size-4" aria-hidden="true" />What the agents are doing</CardTitle></CardHeader>
      <CardContent>
        {!m.data?.length ? <p className="text-sm text-muted-foreground">No hand-overs yet. They appear when a case is opened.</p> : (
          <ol className="flex flex-col gap-1.5 text-sm" data-testid="agent-activity">
            {m.data.map((x) => <li key={x.id}><strong>{WHO[x.sender] ?? x.sender}</strong> {TOPIC[x.topic] ?? x.topic} <span className="num">{x.patient_id ? personLabel(x.patient_id) : ""}</span> <span className="text-muted-foreground">→ {WHO[x.recipient] ?? x.recipient}</span></li>)}
          </ol>)}
        <Link to="/system?tab=how" className="mt-2 inline-block text-sm underline">How the agents work together</Link>
      </CardContent>
    </Card>
  )
}

export default function Today() {
  const patients = usePatients()
  const alerts = useAlertList(50)
  const live = useLive()
  const tasks = useTasks("OPEN")
  const { mode } = useMode()
  const items = patients.data?.items ?? []
  const open = (alerts.data?.items ?? []).filter(isOpen)
  const rank = (s?: string | null) => (s === "URGENT" ? 0 : s === "MONITOR" ? 1 : 2)
  const attention = items.filter((p) => p.attention_kind === "change").sort((a, b) => rank(a.latest_status) - rank(b.latest_status))
  const dataIssues = items.filter((p) => p.attention_kind === "data")
  const fine = items.filter((p) => !attention.includes(p) && !dataIssues.includes(p))
  const prov = new Set((live.data?.patients ?? []).filter((p) => p.provisional).map((p) => p.patient_id))
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">Today</h1>
        <p className="text-sm text-muted-foreground" data-testid="today-summary" aria-live="polite">
          {patients.isLoading ? "Loading…" : `${attention.length} ${attention.length === 1 ? "person needs" : "people need"} attention${dataIssues.length ? ` · ${dataIssues.length} with data to check` : ""} · ${open.length} alert${open.length === 1 ? "" : "s"} waiting for acknowledgement · ${tasks.data?.total ?? 0} open task${tasks.data?.total === 1 ? "" : "s"}`}
        </p>
      </div>
      {patients.isError && <ErrorNote error={patients.error} />}
      <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
        <div className="flex min-w-0 flex-col gap-5">
          {open.length > 0 && (
            <section aria-labelledby="act" className="flex flex-col gap-2"><h2 id="act" className="text-lg font-semibold">Needs action now</h2>
              <div className="flex flex-col gap-2" data-testid="open-alerts">{open.map((a) => <AlertCard key={a.alert_id} a={a} compact />)}</div></section>)}
          <section aria-labelledby="chg" className="flex flex-col gap-2"><h2 id="chg" className="text-lg font-semibold">Changed from their usual</h2>
            {patients.isLoading ? <CardSkeleton h="h-28" /> : attention.length === 0 ? (
              items.length === 0
                ? <Empty title="No data yet">{mode === "emulator" ? "Open Demo tools and start the emulator: events arrive and each person gets a status as the data comes in." : mode === "replay" ? "Open Demo tools and press Play to replay the study." : "Waiting for the first device to send events to /api/ingest/events."}</Empty>
                : <Empty title="Everyone looks as usual">No one differs from their own pattern right now.</Empty>)
              : <ul className="flex flex-col gap-2" data-testid="attention-list">{attention.map((p) => <AttentionRow key={p.patient_id} p={p} provisional={prov.has(p.patient_id)} />)}</ul>}
          </section>
          {dataIssues.length > 0 && (
            <section aria-labelledby="dat" className="flex flex-col gap-2"><h2 id="dat" className="text-lg font-semibold">Data needs checking</h2>
              <p className="text-sm text-muted-foreground">Nothing suggests a change in the person. A sensor or reading is missing, so ECHOS cannot say the day is normal.</p>
              <ul className="flex flex-col gap-2" data-testid="data-list">{dataIssues.map((p) => <DataRow key={p.patient_id} p={p} />)}</ul></section>)}
          {fine.length > 0 && (
            <section aria-labelledby="ok" className="flex flex-col gap-2"><h2 id="ok" className="text-lg font-semibold">As usual</h2>
              <ul className="grid gap-2 sm:grid-cols-2" data-testid="fine-list">
                {fine.map((p) => (
                  <li key={p.patient_id}><Link to={`/patients/${p.patient_id}`} className="flex items-center justify-between gap-2 rounded-xl border p-3 hover:shadow-md" aria-label={`Open ${personLabel(p.patient_id)}`}>
                    <div><div className="font-semibold">{personLabel(p.patient_id)}</div><div className="text-xs text-muted-foreground">age {p.age_band} · {p.sex} · {fmtDay(p.latest_day)}</div></div>
                    <div className="flex flex-col items-end gap-1"><StatusBadge status={p.latest_status} /><Timeline pid={p.patient_id} days={7} /></div></Link></li>))}
              </ul></section>)}
        </div>
        <aside className="flex flex-col gap-4" aria-label="Side information">
          <Tasks />
          <Freshness />
          <Activity_ />
          <p className="flex items-start gap-1.5 text-xs text-muted-foreground"><Activity className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />Decision support, not a diagnosis. Status compares each person with their own usual pattern.</p>
        </aside>
      </div>
    </div>
  )
}
