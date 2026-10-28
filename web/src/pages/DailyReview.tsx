import { AlertTriangle, BellRing, BookOpenText, ChevronLeft, ChevronRight, ClipboardList, HeartPulse } from "lucide-react"
import { Link, useSearchParams } from "react-router-dom"
import EvidenceCard from "@/components/EvidenceCard"
import { CardSkeleton, Empty, ErrorNote } from "@/components/States"
import { Button } from "@/components/ui/button"
import { personLabel } from "@/lib/live"
import { type DayPatient, useDay } from "@/lib/queries"
import { Cite } from "@/lib/refs"

const pc = (v?: number | null, d = 0) => (v == null ? "–" : `${(v * 100).toFixed(d)}%`)
const r0 = (v?: number | null) => (v == null ? "–" : String(Math.round(v)))
const SLOT: Record<number, string> = { 8: "night (18–08)", 12: "morning (08–12)", 18: "afternoon (12–18)" }   // a window is keyed by its end hour

/** One reading against the person's usual: the value, and an arrow when it is more than 2 personal SDs away. */
function Reading({ v, u, z, unit }: { v?: number | null; u?: number | null; z?: number | null; unit?: string }) {
  if (v == null) return <span className="text-muted-foreground">no reading</span>
  const off = z != null && Math.abs(z) >= 2
  return (
    <span className="num whitespace-nowrap">
      <span className={off ? "font-semibold" : ""}>{r0(v)}{unit}</span>
      <span className="text-muted-foreground"> (usual {r0(u)})</span>
      {off && <span className="ml-1 text-xs font-semibold" title={`${z!.toFixed(1)} personal SDs from usual`}>{z! > 0 ? "▲" : "▼"}</span>}
    </span>
  )
}

function Step({ n, icon: Icon, agent, children }: { n: number; icon: typeof HeartPulse; agent: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5">
      <span className="num flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">{n}</span>
      <div><div className="flex items-center gap-1.5 font-semibold"><Icon className="size-4 text-primary" aria-hidden="true" />{agent}</div><p className="text-sm text-muted-foreground">{children}</p></div>
    </div>
  )
}

function topWindow(p: DayPatient) {
  return [...(p.agitation ?? [])].sort((a, b) => (b.p ?? 0) - (a.p ?? 0))[0]
}

export default function DailyReview() {
  const [sp, setSp] = useSearchParams()
  const q = useDay(sp.get("day") ?? undefined)
  const d = q.data
  const days = d?.days ?? []
  const i = d?.day ? days.indexOf(d.day) : -1
  const go = (k: number) => setSp({ day: days[k] })
  const pts = d?.patients ?? []
  const picked = pts.filter((p) => p.rank != null)
  const urgent = pts.filter((p) => p.urgent.length > 0)
  const vb = d?.base_rates.v_bar, ab = d?.base_rates.a_bar
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Daily review</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">One day of real TIHM data<Cite n={1} />: every patient's readings, who the team should review (4 a day), why, and the guideline behind each reason.</p>
        </div>
        {days.length > 0 && (
          <div className="flex items-center gap-1.5" role="group" aria-label="Choose the day">
            <Button variant="outline" size="icon" onClick={() => go(i - 1)} disabled={i <= 0} aria-label="Previous day"><ChevronLeft className="size-4" aria-hidden="true" /></Button>
            <select className="num h-9 rounded-md border bg-card px-2 text-sm font-semibold" value={d?.day ?? ""} onChange={(e) => setSp({ day: e.target.value })} aria-label="Day" data-testid="day-select">
              {days.map((x) => <option key={x} value={x}>{x}</option>)}</select>
            <Button variant="outline" size="icon" onClick={() => go(i + 1)} disabled={i < 0 || i >= days.length - 1} aria-label="Next day"><ChevronRight className="size-4" aria-hidden="true" /></Button>
          </div>)}
      </div>

      <section aria-label="The four agents" className="grid gap-4 rounded-xl border bg-card p-4 sm:grid-cols-2 lg:grid-cols-4" data-testid="steps">
        <Step n={1} icon={HeartPulse} agent="Patient Monitor">Reads each person's day and compares it with their own usual<Cite n={4} />. Red flag → urgent alert.</Step>
        <Step n={2} icon={ClipboardList} agent="Review Coordinator">Ranks everyone by risk above the average person and picks the top {d?.k ?? 4}.</Step>
        <Step n={3} icon={BellRing} agent="Care agent">Sends urgent alerts at once and opens a review task for each pick.</Step>
        <Step n={4} icon={BookOpenText} agent="Evidence agent">Explains each alert and pick with a cited guideline passage<Cite n={[2, 3, 5]} />.</Step>
      </section>

      {q.isError ? <ErrorNote error={q.error} /> : !d ? <CardSkeleton h="h-64" /> : !d.day ? (
        <Empty title="No day scored yet">Start the demo with scripts/start.sh (it replays the first three weeks), or press Play in Demo tools.</Empty>
      ) : (<>
        <section aria-labelledby="s1" className="flex flex-col gap-2">
          <h2 id="s1" className="text-lg font-semibold">1 · Every patient on <span className="num">{d.day}</span></h2>
          <p className="text-sm text-muted-foreground">
            Priority = the larger of <i>BP / pulse risk ÷ {pc(vb)}</i> and <i>agitation risk ÷ {pc(ab, 1)}</i>: how many times above the average person.
            {d.allocated ? ` The ${d.k} highest are reviewed today.` : " This day has not closed yet: the Review Coordinator picks when it does."}
          </p>
          <div className="overflow-x-auto rounded-xl border bg-card">
            <table className="w-full text-sm" data-testid="day-table">
              <thead><tr className="border-b text-left text-xs text-muted-foreground">
                <th className="px-3 py-2 font-medium">Decision</th><th className="px-2 font-medium">Patient</th><th className="px-2 font-medium">Blood pressure</th>
                <th className="px-2 font-medium">Pulse</th><th className="px-2 font-medium">Most unusual activity</th><th className="px-2 font-medium">BP / pulse risk (3 days)</th>
                <th className="px-2 font-medium">Agitation risk</th><th className="px-2 font-medium">Priority</th></tr></thead>
              <tbody>{pts.map((p) => {
                const s = p.vitals.sys ?? {}, di = p.vitals.dia ?? {}, h = p.vitals.hr ?? {}
                const w = topWindow(p)
                const top = p.rank != null
                return (
                  <tr key={p.patient_id} className={`border-b align-top last:border-0 ${p.urgent.length ? "bg-urgent-bg text-urgent-fg" : top ? "bg-accent/40" : ""}`} data-testid={top ? "picked-row" : "patient-row"}>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {top ? <span className="inline-flex items-center rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-primary-foreground">Review #{p.rank}</span>
                        : <span className="text-xs text-muted-foreground">{d.allocated ? "not today" : "–"}</span>}
                      {p.urgent.length > 0 && <div className="mt-1 inline-flex items-center gap-1 text-xs font-semibold"><AlertTriangle className="size-3.5" aria-hidden="true" />URGENT alert</div>}
                    </td>
                    <td className="px-2 py-2 whitespace-nowrap"><Link to={`/patients/${p.patient_id}`} className="num font-semibold underline">{personLabel(p.patient_id)}</Link>
                      {p.injected && <div className="text-xs">injected reading</div>}</td>
                    <td className="px-2 py-2">{s.value == null ? <span className="text-muted-foreground">no reading</span> : (
                      <span className="num whitespace-nowrap"><span className={Math.abs(s.z ?? 0) >= 2 || Math.abs(di.z ?? 0) >= 2 ? "font-semibold" : ""}>{r0(s.value)}/{r0(di.value)}</span>
                        <span className="text-muted-foreground"> (usual {r0(s.usual)}/{r0(di.usual)})</span>
                        {Math.abs(s.z ?? 0) >= 2 && <span className="ml-1 text-xs font-semibold" title={`systolic ${s.z!.toFixed(1)} personal SDs from usual`}>{s.z! > 0 ? "▲" : "▼"}</span>}</span>)}</td>
                    <td className="px-2 py-2"><Reading v={h.value} u={h.usual} z={h.z} /></td>
                    <td className="px-2 py-2">{w ? <span><span className="num font-medium">{pc(w.p, 1)}</span> {SLOT[w.slot] ?? ""}{w.reasons?.[0] ? <span className="text-muted-foreground">: {w.reasons[0]}</span> : ""}</span> : <span className="text-muted-foreground">no sensor data</span>}</td>
                    <td className="num px-2 py-2">{pc(p.vitals_risk)}</td>
                    <td className="num px-2 py-2">{pc(p.agitation_risk, 1)}</td>
                    <td className="num px-2 py-2 font-semibold">{p.priority != null ? `${p.priority.toFixed(1)}×` : "–"}</td>
                  </tr>)
              })}</tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground">▲ / ▼ = more than 2 of this person's own standard deviations from their usual. Red rows crossed a red-flag limit (NEWS2<Cite n={2} />, BP ≥ 180/120<Cite n={3} />).</p>
        </section>

        <section aria-labelledby="s2" className="flex flex-col gap-3">
          <h2 id="s2" className="text-lg font-semibold">2 · Today's {d.k} reviews: why, and the evidence</h2>
          {!picked.length ? <Empty title="No reviews picked yet">The Review Coordinator picks when the day closes.</Empty> : (
            <div className="grid gap-4 lg:grid-cols-2" data-testid="picks">{picked.map((p) => (
              <article key={p.patient_id} className="flex flex-col gap-2.5 rounded-xl border bg-card p-4" data-testid="pick">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2"><span className="num flex size-7 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">{p.rank}</span>
                    <Link to={`/patients/${p.patient_id}`} className="num text-lg font-semibold underline">{personLabel(p.patient_id)}</Link></div>
                  <span className="num text-sm"><span className="font-semibold">{p.priority?.toFixed(1)}×</span> <span className="text-muted-foreground">{p.pick_reason === "fairness" ? "fairness: not reviewed for a week" : (p.lift_agitation ?? 0) > (p.lift_vitals ?? 0) ? "driven by agitation" : "driven by BP / pulse"}</span></span>
                </div>
                <div>
                  <div className="text-xs font-semibold text-muted-foreground uppercase">Why (Patient Monitor)</div>
                  <ul className="mt-1 list-disc pl-5 text-sm">{(p.reasons.length ? p.reasons : ["no specific reason recorded"]).map((x) => <li key={x}>{x}</li>)}</ul>
                </div>
                <div>
                  <div className="mb-1 text-xs font-semibold text-muted-foreground uppercase">Guideline (Evidence agent)</div>
                  <EvidenceCard card={p.evidence.find((c) => c.kind === "review") ?? p.evidence[0]} />
                </div>
                {p.outcome && <p className="text-xs text-muted-foreground">Study record for this day: <span className="font-semibold">{p.outcome === "CONFIRMED" ? "an event was confirmed" : "no event"}</span></p>}
              </article>))}
            </div>)}
        </section>

        <section aria-labelledby="s3" className="flex flex-col gap-3">
          <h2 id="s3" className="text-lg font-semibold">3 · Urgent alerts on this day</h2>
          {!urgent.length ? <p className="rounded-xl border bg-card p-4 text-sm text-muted-foreground" data-testid="no-urgent">No reading crossed a red-flag limit on this day, so no urgent alert was sent.</p> : (
            <div className="grid gap-4 lg:grid-cols-2" data-testid="urgent">{urgent.map((p) => (
              <article key={p.patient_id} className="flex flex-col gap-2 rounded-xl border border-urgent-line bg-card p-4">
                <div className="flex items-center gap-2 font-semibold text-urgent-fg"><AlertTriangle className="size-4" aria-hidden="true" />
                  <Link to={`/patients/${p.patient_id}`} className="num underline">{personLabel(p.patient_id)}</Link></div>
                <ul className="list-disc pl-5 text-sm">{p.urgent.map((u) => <li key={u}>{u}</li>)}</ul>
                <p className="text-xs text-muted-foreground">Sent at once by the Care agent to the caregiver and clinician (dry run: no real e-mail). <Link to="/alerts" className="underline">Alerts</Link></p>
                <EvidenceCard card={p.evidence.find((c) => c.kind === "urgent")} />
              </article>))}
            </div>)}
        </section>

        <p className="text-xs text-muted-foreground">Each person is scored by a model trained without them (5-fold cross-validation by patient). How well it works and the full method: <Link to="/report" className="underline">Report & references</Link>.</p>
      </>)}
    </div>
  )
}
