import { type ReactNode } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import EvidenceCard from "@/components/EvidenceCard"
import { CardSkeleton, Empty, ErrorNote } from "@/components/States"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { pct, personLabel } from "@/lib/live"
import { useMemoryCard, useMonitor, usePatients, useReview, useTrace } from "@/lib/queries"
import { StatusBadge } from "@/lib/status"

function Step({ n, title, agent, children }: { n: number; title: string; agent: string; children: ReactNode }) {
  return (
    <li className="flex gap-3" data-testid={`trace-step-${n}`}>
      <div className="flex flex-col items-center"><span className="num flex size-8 shrink-0 items-center justify-center rounded-full border-2 border-primary font-semibold">{n}</span><span className="mt-1 w-px flex-1 bg-border" aria-hidden="true" /></div>
      <div className="mb-4 min-w-0 flex-1 rounded-xl border bg-card p-3 text-sm">
        <div className="mb-1.5 flex flex-wrap items-baseline gap-2"><h3 className="text-base font-semibold">{title}</h3><span className="text-xs text-muted-foreground">{agent}</span></div>
        {children}
      </div>
    </li>
  )
}
const N = ({ v, d = 0 }: { v?: number | null; d?: number }) => <span className="num font-semibold">{v == null ? "n/a" : v.toFixed(d)}</span>
const NAMES: Record<string, string> = { sys: "Systolic", dia: "Diastolic", hr: "Pulse" }
const SLOT: Record<number, string> = { 8: "night", 12: "morning", 18: "afternoon" }

export default function Trace() {
  const { pid, day } = useParams()
  const nav = useNavigate()
  const patients = usePatients()
  const review = useReview()
  // default: the most informative card of the latest review day (an urgent or personal-baseline reason beats an activity-only one)
  const pick = review.data?.items.find((i) => i.why.some((w) => /URGENT|vs usual/.test(w))) ?? review.data?.items[0]
  const cur = pid ?? pick?.patient_id ?? patients.data?.items[0]?.patient_id
  const monitor = useMonitor(cur ?? "", 60)
  const days = (monitor.data ?? []).map((d) => d.day)
  const curDay = day ?? (pid ? days[0] : pick?.day) ?? days[0]
  const t = useTrace(cur, curDay)
  const mem = useMemoryCard(cur)
  const m = t.data?.monitor as Record<string, any> | null | undefined
  const rq = t.data?.review_request as Record<string, any> | null | undefined
  const rv = t.data?.review as Record<string, any> | null | undefined
  const co = t.data?.coordinator as Record<string, any> | null | undefined
  const go = (p: string, d?: string) => nav(`/trace/${p}${d ? `/${d}` : ""}`)
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">Decision trace</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">Everything the agents did for one person on one day, in order. Nothing here is a hidden per-patient model: the shared models are trained once, and each person only has the small memory on the right.</p>
      </div>
      <div className="flex flex-wrap items-end gap-3" role="group" aria-label="Choose a person and a day">
        <label className="flex flex-col gap-1 text-sm font-medium">Person
          <select className="rounded-md border bg-card px-2 py-1.5" value={cur ?? ""} onChange={(e) => go(e.target.value)} data-testid="trace-patient">
            {(patients.data?.items ?? []).map((p) => <option key={p.patient_id} value={p.patient_id}>{personLabel(p.patient_id)}</option>)}</select></label>
        <label className="flex flex-col gap-1 text-sm font-medium">Day
          <select className="num rounded-md border bg-card px-2 py-1.5" value={curDay ?? ""} onChange={(e) => go(cur!, e.target.value)} data-testid="trace-day">
            {days.map((d) => <option key={d} value={d}>{d}</option>)}</select></label>
        {t.data?.status && <StatusBadge status={t.data.status} />}
      </div>
      {!cur || !curDay ? <Empty title="Nothing to trace yet">Press Play in Demo tools: the first scored day appears in a few seconds.</Empty> : t.isError ? <ErrorNote error={t.error} /> : !t.data ? <CardSkeleton h="h-64" /> : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
          <ol aria-label="Steps" data-testid="trace">
            <Step n={1} title="What came in" agent="Replay → Patient Monitor">
              {m ? <p>Readings of the day (daily maximum): {(["sys", "dia", "hr"] as const).map((v) => <span key={v} className="mr-3">{NAMES[v]} <N v={m.vitals?.[v]?.value} /></span>)}and <N v={(m.agitation ?? []).length} /> activity window(s). The model never sees tomorrow: only this person's past readings are used.</p> : <p className="text-muted-foreground">No scored day found.</p>}
              {m && (m.quality?.flags ?? []).length > 0
                ? <p className={`mt-2 rounded-md border px-2 py-1 ${m.quality.device_problem ? "border-monitor-line bg-monitor-bg text-monitor-fg" : "border-gap-line bg-gap-bg text-gap-fg"}`} data-testid="data-check"><b>Data check:</b> {(m.quality.flags as string[]).join("; ")}.</p>
                : m && <p className="mt-2 text-xs text-muted-foreground" data-testid="data-check">Data check: sensors and readings look normal for this person.</p>}
            </Step>
            <Step n={2} title="How unusual, for this person" agent="Personal baseline (Normal-Inverse-Gamma)">
              <ul className="num grid gap-1 sm:grid-cols-3">{(["sys", "dia", "hr"] as const).map((v) => { const r = m?.vitals?.[v]; return (
                <li key={v} className="rounded-md border p-2"><div className="font-sans text-xs text-muted-foreground">{NAMES[v]}</div>
                  {r?.value == null ? <div>no reading</div> : <><div><b>{r.value}</b> vs usual <b>{r.usual}</b></div><div className="text-xs text-muted-foreground">z = {r.z} · from {r.n_past} past readings</div></>}</li>) })}</ul>
            </Step>
            <Step n={3} title="Red-flag rules" agent="Alert rules, no model">
              {(m?.safety ?? []).length ? <ul className="list-disc pl-5 font-medium text-urgent-fg">{(m!.safety as string[]).map((x) => <li key={x}>URGENT: {x}</li>)}</ul> : <p>No red flag. </p>}
              {(m?.attention ?? []).length > 0 && <ul className="list-disc pl-5 text-muted-foreground">{(m!.attention as string[]).map((x) => <li key={x}>attention: {x}</li>)}</ul>}
            </Step>
            <Step n={4} title="Agitation probability per window" agent="Agitation detector (XGBoost + Platt)">
              {(m?.agitation ?? []).length ? <ul className="num flex flex-col gap-1">{(m!.agitation as any[]).map((w) => <li key={w.w}><span className="font-sans">{SLOT[w.slot] ?? w.slot} window</span>: <b>{pct(w.p, 1)}</b> · {w.events} sensor events{w.reasons?.length ? <span className="font-sans text-muted-foreground"> · {w.reasons.join("; ")}</span> : null}</li>)}</ul> : <p className="text-muted-foreground">No activity windows this day.</p>}
            </Step>
            <Step n={5} title="Personal event rate and 3-day risk" agent="Patient Monitor (Beta-Bernoulli), after the day's feedback">
              {rq ? <p><b className="num">{rq.events_k}</b> confirmed BP/pulse event days in <b className="num">{rq.days_n}</b> days → personal rate <b className="num">{pct(rq.rate)}</b> (95% interval {(rq.rate_ci95 ?? []).map((x: number) => pct(x)).join("–")}) → <b>3-day risk <span className="num">{pct(rq.vitals_risk)}</span></b>. The prior is the population's rate and is worth only a few days, so a new person becomes personal quickly.</p> : <p className="text-muted-foreground">The review request is sent after the day's outcome arrives.</p>}
            </Step>
            <Step n={6} title="Lift and allocation" agent="Review Coordinator">
              {rv ? <p>Lift over base rate: BP/pulse <N v={rv.lift_vitals} d={1} />×, agitation <N v={rv.lift_agitation} d={1} />× → priority <N v={rv.priority} d={2} />. <b>Rank {rv.rank}</b> of {co?.k ?? 4} ({rv.reason === "fairness" ? "fairness: unseen for a week" : "by priority"}). {co && <>Deferred today: <span className="num">{(co.deferred ?? []).length}</span>.</>}</p> : <p className="text-muted-foreground">Not among today's {co?.k ?? 4} reviews.{co ? ` ${(co.selected ?? []).length} were selected.` : ""}</p>}
            </Step>
            <Step n={7} title="Messages on the bus" agent="The agents never call each other">
              {t.data.messages.length ? <ul className="num flex flex-col gap-0.5 text-xs">{t.data.messages.map((x) => <li key={x.id}>{x.sender} → {x.recipient}: <b>{x.topic}</b> <span className="text-muted-foreground">({x.status})</span></li>)}</ul> : <p className="text-muted-foreground">None for this day.</p>}
            </Step>
            <Step n={8} title="Evidence, alert and task" agent="Evidence agent · Care agent">
              {t.data.evidence.length ? <div className="flex flex-col gap-2">{t.data.evidence.map((c) => <EvidenceCard key={c.card_id} card={c} />)}</div> : <p className="text-muted-foreground">No evidence card yet for this day.</p>}
              {t.data.alerts.length > 0 && <p className="mt-2">Alert: <span className="num">{t.data.alerts.map((a) => a.alert_id.split("|")[2]).join(", ")}</span> <Link to="/alerts" className="underline">open Alerts</Link></p>}
              {t.data.care_task && <p className="mt-2">Review task: {String(t.data.care_task.title)} ({String(t.data.care_task.status)})</p>}
            </Step>
          </ol>
          <aside aria-label="Memory"><Card data-testid="memory-card"><CardHeader><CardTitle>What this agent remembers</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-2 text-sm">
              {!mem.data?.known ? <p className="text-muted-foreground">Nothing yet.</p> : <>
                <p className="num text-2xl font-semibold">{mem.data.numbers_stored} numbers</p>
                <ul className="num flex flex-col gap-0.5 text-xs">
                  <li>last 28 readings × 3 vitals: {Object.values(mem.data.vitals_history ?? {}).reduce((x, y) => x + y.length, 0)}</li>
                  <li>events k / days n: {mem.data.events_k} / {mem.data.days_n}</li>
                  <li>activity windows seen: {mem.data.windows_seen}</li>
                  <li>confirmed agitation episodes: {mem.data.episodes}</li>
                  <li>same-slot history: {Object.values(mem.data.slot_history_windows ?? {}).reduce((x, y) => x + y, 0)} windows</li></ul>
                <p className="text-xs text-muted-foreground">Shared models: 5 cross-validation folds plus one for everyone, trained once. This person is scored by the fold that excluded them. Models trained per patient: 0.</p></>}
            </CardContent></Card></aside>
        </div>)}
    </div>
  )
}
