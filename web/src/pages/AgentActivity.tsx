import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { Bot, Cpu, Settings2 } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { CardSkeleton, ErrorNote } from "@/components/States"
import { ago, personLabel, useNow } from "@/lib/live"
import { useActivity, useAgentSummary, useAlertList, usePatients } from "@/lib/queries"
import { cn } from "@/lib/utils"
import type { ActivityEvent, AgentSummary } from "@/lib/api"

const KIND_CLS: Record<string, string> = {
  detector: "bg-gap-bg text-gap-fg border-gap-line",
  agent: "bg-normal-bg text-normal-fg border-normal-line",
  infra: "bg-secondary text-secondary-foreground border-border",
}
const KIND_ICON = { detector: Cpu, agent: Bot, infra: Settings2 } as const
const STATUS_CLS: Record<string, string> = { running: "text-normal-fg", idle: "text-muted-foreground", missing: "text-urgent-fg", stopped: "text-urgent-fg", "n/a": "text-muted-foreground" }
const KIND_OF: Record<string, string> = { quality: "detector", health_monitoring: "detector", fall: "detector", dispatcher: "infra", supervisor: "infra", watchdog: "infra" }
const NAME: Record<string, string> = { quality: "Data Quality", health_monitoring: "Vitals + Behaviour", fall: "Fall detector", triage: "Triage", dispatcher: "Alert dispatcher", investigator: "Case Investigator", coordinator: "Care Coordinator", report: "Report agent", supervisor: "Supervisor", watchdog: "Watchdog" }
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })

function Chip({ agent }: { agent: string }) {
  return <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-xs font-semibold", KIND_CLS[KIND_OF[agent] ?? "agent"])}>{NAME[agent] ?? agent}</span>
}

function AgentCard({ a, active, onPick, now }: { a: AgentSummary; active: boolean; onPick: () => void; now: number }) {
  const Icon = KIND_ICON[a.kind as keyof typeof KIND_ICON] ?? Bot
  return (
    <li>
      <button type="button" onClick={onPick} aria-pressed={active} data-testid={`agent-card-${a.id}`}
        className={cn("flex h-full w-full flex-col gap-1 rounded-xl border bg-card p-3 text-left text-sm transition-shadow hover:shadow-md", active && "ring-2 ring-primary")}>
        <span className="flex items-center justify-between gap-2"><span className="flex items-center gap-1.5 font-semibold"><Icon className="size-4" aria-hidden="true" />{a.name}</span>
          <span className={cn("text-xs font-semibold", STATUS_CLS[a.status])} data-testid="agent-status">{a.status}</span></span>
        <span className="text-xs text-muted-foreground">{a.actions} action{a.actions === 1 ? "" : "s"} logged{a.last_ts ? ` · last ${ago(now - a.last_ts)}` : ""}</span>
        <span className="line-clamp-2 text-xs">{a.last_text ?? a.detail ?? "Nothing logged yet."}</span>
      </button>
    </li>
  )
}

function EventRow({ e, now, rel, t0 }: { e: ActivityEvent; now: number; rel?: boolean; t0?: number }) {
  return (
    <li className="flex flex-wrap items-start gap-x-2 gap-y-1 border-b py-2 text-sm last:border-b-0" data-testid="activity-row" data-agent={e.agent}>
      <span className="num w-20 shrink-0 text-xs text-muted-foreground" title={new Date(e.ts * 1000).toLocaleString()}>{rel && t0 != null ? `+${(e.ts - t0).toFixed(1)} s` : clock(e.ts)}</span>
      <Chip agent={e.agent} />
      <span className="min-w-0 flex-1">{e.text}
        {!rel && e.patient_id && <> · <Link to={`/patients/${e.patient_id}`} className="underline">{personLabel(e.patient_id)}</Link>{e.day ? ` · ${e.day}` : ""}</>}
        {!rel && <span className="text-xs text-muted-foreground"> ({ago(now - e.ts)})</span>}</span>
    </li>
  )
}

export default function AgentActivity() {
  const now = useNow(2000)
  const [agent, setAgent] = useState<string | undefined>()
  const [sel, setSel] = useState<string>("")
  const sum = useAgentSummary()
  const feed = useActivity({ agent, limit: 80 })
  const patients = usePatients()
  const alerts = useAlertList(30)
  const cases = useMemo(() => {
    const m = new Map<string, string>()
    for (const a of alerts.data?.items ?? []) { if (!a.event_time) continue; const d = a.event_time.slice(0, 10); m.set(`${a.patient_id}|${d}`, `${personLabel(a.patient_id)} · ${d} · ${a.type === "fall" ? "fall alert" : "critical vitals alert"}`) }
    for (const p of patients.data?.items ?? []) if (p.latest_day && p.latest_status && p.latest_status !== "NORMAL" && !m.has(`${p.patient_id}|${p.latest_day}`)) m.set(`${p.patient_id}|${p.latest_day}`, `${personLabel(p.patient_id)} · ${p.latest_day} · ${p.latest_status.toLowerCase()} day`)
    return [...m.entries()]
  }, [alerts.data, patients.data])
  const chosen = sel || cases[0]?.[0] || ""
  const [cp, cd] = chosen.split("|")
  const flow = useActivity({ pid: cp || undefined, day: cd || undefined, oldestFirst: true, limit: 200 })
  const t0 = flow.data?.[0]?.ts
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">Agent activity</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">What each detector and agent has done, taken from the records they write (not a separate log). Detectors sense, agents decide and act, infrastructure delivers and restarts. Nothing here can change what they do.</p>
      </div>
      {sum.isError && <ErrorNote error={sum.error} />}
      <section aria-labelledby="who" className="flex flex-col gap-2">
        <h2 id="who" className="text-lg font-semibold">Who is doing what <span className="text-sm font-normal text-muted-foreground">(select one to filter the timeline)</span></h2>
        {!sum.data ? <CardSkeleton h="h-32" /> : <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5" data-testid="agent-cards">{sum.data.map((a) => <AgentCard key={a.id} a={a} now={now} active={agent === a.id} onPick={() => setAgent(agent === a.id ? undefined : a.id)} />)}</ul>}
      </section>
      <Card>
        <CardHeader><CardTitle>Follow one case</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3">
          {!cases.length ? <p className="text-sm text-muted-foreground" data-testid="flow-empty">No alert or changed day yet. When one appears, pick it here to see each hand-over from detector to report in order.</p> : (
            <>
              <label className="flex flex-wrap items-center gap-2 text-sm"><span className="font-medium">Case</span>
                <select className="rounded-md border bg-card px-2 py-1.5" value={chosen} onChange={(e) => setSel(e.target.value)} data-testid="case-select" aria-label="Case to follow">
                  {cases.map(([k, label]) => <option key={k} value={k}>{label}</option>)}</select></label>
              {flow.isError && <ErrorNote error={flow.error} />}
              {!flow.data ? <CardSkeleton h="h-24" /> : !flow.data.length ? <p className="text-sm text-muted-foreground">Nothing recorded for this case.</p> : (
                <ol data-testid="flow" aria-label="Steps of this case, oldest first">{flow.data.map((e, i) => <EventRow key={i} e={e} now={now} rel t0={t0} />)}</ol>)}
              <p className="text-xs text-muted-foreground">Times are seconds after the first step, on this machine's clock. A step missing here means that part did not run or write a record for this case (for example, a replay made before the Case Investigator existed has no case file). In live and emulator mode the data-quality and vitals / behaviour checks run inside the live processor, so they appear as the reasons in Triage's decision rather than as separate detector steps.</p>
            </>)}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Latest activity{agent ? `: ${NAME[agent] ?? agent}` : ""}</CardTitle></CardHeader>
        <CardContent>
          {feed.isError && <ErrorNote error={feed.error} />}
          {!feed.data ? <CardSkeleton h="h-32" /> : !feed.data.length ? <p className="text-sm text-muted-foreground" data-testid="feed-empty">{agent ? "This part has not logged anything yet." : "Nothing has happened yet. Start the replay or the emulator in Demo tools."}</p> : <ol data-testid="feed">{feed.data.map((e, i) => <EventRow key={i} e={e} now={now} />)}</ol>}
        </CardContent>
      </Card>
    </div>
  )
}
