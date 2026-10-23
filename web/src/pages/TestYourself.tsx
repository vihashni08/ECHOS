import { useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { CardSkeleton, ErrorNote } from "@/components/States"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useDemoAudit, useDemoBudget, useDemoGame, type GameDay } from "@/lib/queries"

const TABS = [{ id: "audit", label: "1. Why no detector" }, { id: "game", label: "2. You vs ECHOS" }, { id: "budget", label: "3. The budget is a decision" }] as const

// ---------------------------------------------------------------- 1. the label audit as one picture
function Audit() {
  const a = useDemoAudit()
  if (a.isError) return <ErrorNote error={a.error} />
  if (!a.data) return <CardSkeleton h="h-72" />
  const { cells, wall } = a.data
  const X0 = 90, Y0 = 40, CW = 18, CH = 14, W = 30, H = 20, maxn = Math.max(...cells.map((c) => c.n))
  const x = (s: number) => 44 + ((s - X0) / 5) * CW + CW / 2
  const y = (d: number) => 8 + (H - 1 - (d - Y0) / 5) * CH + CH / 2
  return (
    <Card data-testid="audit">
      <CardHeader><CardTitle>Almost every day above 160/100 carries a BP label</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <p><b className="num">{(wall.share_labelled * 100).toFixed(0)}%</b> of the <span className="num">{wall.days}</span> days at or above 160 systolic or 100 diastolic are labelled; only <b className="num">{(wall.below_share_labelled * 100).toFixed(1)}%</b> of the <span className="num">{wall.below_days}</span> days below it are. The labels are confirmed threshold alerts, so a model that "detects" them from the same day's readings re-learns the threshold. That is why thresholds stay rules in ECHOS and the models rank who to review.</p>
        <figure className="overflow-x-auto" role="img" aria-label={`Each circle is a group of patient-days at a systolic and diastolic reading. Circle size is the number of days; darker means more of them carry a BP label. Labelled days sit almost entirely at or above 160 systolic or 100 diastolic.`}>
          <svg viewBox={`0 0 ${44 + W * CW + 8} ${8 + H * CH + 34}`} className="h-auto w-full min-w-[520px] max-w-3xl" aria-hidden="true">
            {cells.map((c) => { const share = c.labelled / c.n; return (
              <circle key={`${c.sys}-${c.dia}`} cx={x(c.sys)} cy={y(c.dia)} r={2.2 + 6.5 * Math.sqrt(c.n / maxn)} fill="var(--chart-1)" fillOpacity={0.12 + 0.88 * share} stroke="var(--chart-1)" strokeWidth={0.6} />) })}
            <line x1={x(160) - CW / 2} x2={x(160) - CW / 2} y1={4} y2={8 + H * CH} stroke="var(--chart-2)" strokeWidth={1.5} strokeDasharray="4 3" />
            <line x1={44} x2={44 + W * CW} y1={y(100) - CH / 2} y2={y(100) - CH / 2} stroke="var(--chart-2)" strokeWidth={1.5} strokeDasharray="4 3" />
            <text x={x(160) + 4} y={14} fontSize={11} fill="var(--foreground)">systolic 160</text>
            <text x={48} y={y(100) - CH / 2 - 3} fontSize={11} fill="var(--foreground)">diastolic 100</text>
            {[100, 120, 140, 160, 180, 200, 220].map((s) => <text key={s} x={x(s)} y={8 + H * CH + 14} fontSize={10} textAnchor="middle" fill="var(--muted-foreground)">{s}</text>)}
            {[50, 70, 90, 110, 130].map((d) => <text key={d} x={40} y={y(d) + 3} fontSize={10} textAnchor="end" fill="var(--muted-foreground)">{d}</text>)}
            <text x={44 + (W * CW) / 2} y={8 + H * CH + 30} fontSize={11} textAnchor="middle" fill="var(--muted-foreground)">systolic (mmHg), daily maximum</text>
          </svg>
          <figcaption className="text-xs text-muted-foreground">Circle size = number of patient-days; darker = larger share carrying a BP label. Vertical axis: diastolic (mmHg). Dashed lines: 160 and 100.</figcaption>
        </figure>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------- 2. You vs ECHOS
type Pick = { you: number; yourSplit: { a: number; v: number }; d: GameDay }
function Game() {
  const g = useDemoGame()
  const [i, setI] = useState(0)
  const [sel, setSel] = useState<string[]>([])
  const [shown, setShown] = useState(false)
  const [done, setDone] = useState<Pick[]>([])
  if (g.isError) return <ErrorNote error={g.error} />
  if (!g.data) return <CardSkeleton h="h-72" />
  const finished = i >= g.data.days.length
  const d = g.data.days[Math.min(i, g.data.days.length - 1)]
  const k = d.k
  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length < k ? [...s, id] : s))
  const score = (ids: string[]) => ({ a: d.patients.filter((p) => ids.includes(p.id)).reduce((t, p) => t + p.agitation_episodes, 0), v: d.patients.filter((p) => ids.includes(p.id)).reduce((t, p) => t + p.vitals_events_next3, 0) })
  const reveal = () => { const s = score(sel); setShown(true); setDone((x) => [...x.filter((y) => y.d.day !== d.day), { you: s.a + s.v, yourSplit: { a: s.a, v: s.v }, d }]) }
  const next = () => { setI(i + 1); setSel([]); setShown(false) }
  if (finished) return <GameSummary done={done} restart={() => { setI(0); setSel([]); setShown(false); setDone([]) }} />
  const you = score(sel)
  return (
    <Card data-testid="game">
      <CardHeader><CardTitle>Day {i + 1} of {g.data.days.length} · {d.day}</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <p>You are the clinical team and have <b>{k} reviews</b> today for <span className="num">{d.patients.length}</span> people. Pick {k} using only what is shown. Then see what TIHM's monitoring team confirmed: agitation that day, and BP/pulse events in the next 3 days.</p>
        <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-sm" data-testid="game-table">
          <thead><tr className="text-left text-xs text-muted-foreground"><th className="py-1 font-medium">Pick</th><th className="font-medium">Person</th><th className="font-medium">BP</th><th className="font-medium">Pulse</th><th className="font-medium">Systolic vs their usual</th><th className="font-medium">Sensor events today</th><th className="font-medium">Confirmed events, 14 d</th>{shown && <th className="font-medium">Found</th>}</tr></thead>
          <tbody>{d.patients.map((p) => { const on = sel.includes(p.id); return (
            <tr key={p.id} className={`border-t ${on ? "bg-accent" : ""}`}>
              <td className="py-1"><input type="checkbox" checked={on} onChange={() => toggle(p.id)} disabled={shown || (!on && sel.length >= k)} aria-label={`Pick ${p.id}`} /></td>
              <td className="num font-semibold">{p.id}</td>
              <td className="num">{p.sys == null ? "no reading" : `${p.sys}/${p.dia}`}{p.tihm_flag ? <span className="ml-1 rounded border border-monitor-line bg-monitor-bg px-1 font-sans text-xs text-monitor-fg">past TIHM limit</span> : null}</td>
              <td className="num">{p.hr ?? "–"}</td>
              <td className="num">{p.sys_z == null ? "–" : `${p.sys_z > 0 ? "+" : ""}${p.sys_z.toFixed(1)} SD`}</td>
              <td className="num">{p.activity ?? "–"}{p.activity_z != null && Math.abs(p.activity_z) >= 2 ? <span className="ml-1 font-sans text-xs text-muted-foreground">({p.activity_z > 0 ? "+" : ""}{p.activity_z.toFixed(1)} vs usual)</span> : null}</td>
              <td className="num">{p.events_14d}</td>
              {shown && <td className="num font-semibold">{p.found}{p.found ? <span className="font-sans text-xs font-normal text-muted-foreground"> ({p.agitation_episodes} agitation, {p.vitals_events_next3} BP/pulse)</span> : null}</td>}
            </tr>) })}</tbody>
        </table></div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="num" aria-live="polite">{sel.length} of {k} picked</span>
          {!shown ? <Button onClick={reveal} disabled={sel.length !== k} data-testid="reveal">Reveal what happened</Button> : <Button onClick={next} data-testid="next-day">{i + 1 < g.data.days.length ? "Next day" : "See the summary"}</Button>}
        </div>
        {shown && <Reveal d={d} you={you} />}
      </CardContent>
    </Card>
  )
}

function Bar({ label, v, max, split, strong }: { label: string; v: number; max: number; split?: { a: number; v: number }; strong?: boolean }) {
  return (
    <li className="grid grid-cols-[9.5rem_1fr_auto] items-center gap-2">
      <span className={strong ? "font-semibold" : ""}>{label}</span>
      <span className="h-3 overflow-hidden rounded bg-muted" aria-hidden="true"><span className="block h-full rounded" style={{ width: `${(100 * v) / Math.max(max, 1)}%`, background: strong ? "var(--chart-2)" : "var(--chart-1)" }} /></span>
      <span className="num text-xs">{Number.isInteger(v) ? v : v.toFixed(1)}{split ? ` (${split.a} agitation, ${split.v} BP/pulse)` : ""}</span>
    </li>)
}
function Reveal({ d, you }: { d: GameDay; you: { a: number; v: number } }) {
  return (
    <ul className="flex flex-col gap-1.5 rounded-lg border p-3 text-sm" data-testid="reveal-result" aria-label="Events found by each way of choosing">
      <Bar label="You" v={you.a + you.v} max={d.best_possible} split={you} strong />
      <Bar label="ECHOS" v={d.echos_found} max={d.best_possible} split={{ a: d.echos_split.agitation, v: d.echos_split.vitals }} />
      <Bar label="Review past BP limits" v={d.threshold_found} max={d.best_possible} split={{ a: d.threshold_split.agitation, v: d.threshold_split.vitals }} />
      <Bar label="Random (average)" v={d.random_found} max={d.best_possible} />
      <Bar label="Best possible" v={d.best_possible} max={d.best_possible} />
    </ul>)
}
function GameSummary({ done, restart }: { done: Pick[]; restart: () => void }) {
  const sum = (f: (p: Pick) => number) => done.reduce((t, p) => t + f(p), 0)
  const rows = [
    { n: "You", t: sum((p) => p.you), a: sum((p) => p.yourSplit.a), v: sum((p) => p.yourSplit.v) },
    { n: "ECHOS", t: sum((p) => p.d.echos_found), a: sum((p) => p.d.echos_split.agitation), v: sum((p) => p.d.echos_split.vitals) },
    { n: "Review past BP limits", t: sum((p) => p.d.threshold_found), a: sum((p) => p.d.threshold_split.agitation), v: sum((p) => p.d.threshold_split.vitals) },
    { n: "Random (average)", t: sum((p) => p.d.random_found), a: NaN, v: NaN },
    { n: "Best possible", t: sum((p) => p.d.best_possible), a: NaN, v: NaN },
  ]
  return (
    <Card data-testid="game-summary"><CardHeader><CardTitle>Summary over {done.length} days</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <table className="w-full max-w-xl"><thead><tr className="text-left text-xs text-muted-foreground"><th className="py-1 font-medium">Chosen by</th><th className="font-medium">Events found</th><th className="font-medium">Agitation</th><th className="font-medium">BP / pulse</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.n} className="border-t"><td className="py-1 font-medium">{r.n}</td><td className="num">{Number.isInteger(r.t) ? r.t : r.t.toFixed(1)}</td><td className="num">{Number.isNaN(r.a) ? "–" : r.a}</td><td className="num">{Number.isNaN(r.v) ? "–" : r.v}</td></tr>)}</tbody></table>
        <p>What this shows, honestly: a plain "review whoever is past the BP limits" rule is hard to beat on BP/pulse events, because the BP labels are threshold alerts. It finds almost no agitation. ECHOS finds fewer BP events but covers agitation too, so it is a <b>balanced</b> policy for a team responsible for both. Neither comes close to the best possible, and 10 days is a small sample. Over all 92 replay days at 4 reviews a day the totals are 0.471 (BP-limit rule), 0.452 (ECHOS) and 0.250 (random) events per review.</p>
        <Button onClick={restart} variant="outline" className="self-start">Play again</Button>
      </CardContent></Card>)
}

// ---------------------------------------------------------------- 3. the budget is a decision
const SHOW = ["Random", "Round robin", "Review past TIHM BP limits", "Vitals only", "Agitation only"]
function BudgetView() {
  const b = useDemoBudget()
  const [k, setK] = useState(4)
  const [fair, setFair] = useState<"" | "3" | "7" | "14">("7")
  const rows = useMemo(() => {
    if (!b.data) return []
    const echos = fair ? `ECHOS + fairness ${fair} d` : "ECHOS"
    return [...SHOW, echos].map((n) => ({ name: n === echos ? echos : n, r: b.data!.policies[n][k - 1], echos: n === echos }))
  }, [b.data, k, fair])
  if (b.isError) return <ErrorNote error={b.error} />
  if (!b.data) return <CardSkeleton h="h-72" />
  const max = Math.max(...rows.map((x) => x.r.events_per_review))
  return (
    <Card data-testid="budget"><CardHeader><CardTitle>How many reviews, and how fair?</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <p>Replay of 56 people over 92 days with out-of-fold scores. Move the budget and the fairness floor and watch what changes.</p>
        <div className="flex flex-wrap items-end gap-6">
          <label className="flex flex-col gap-1 font-medium">Reviews per day (K): <span className="num text-lg" aria-live="polite">{k}</span>
            <input type="range" min={1} max={10} value={k} onChange={(e) => setK(+e.target.value)} aria-label="Reviews per day" data-testid="k-slider" /></label>
          <label className="flex flex-col gap-1 font-medium">Fairness floor
            <select className="rounded-md border bg-card px-2 py-1.5" value={fair} onChange={(e) => setFair(e.target.value as "" | "3" | "7" | "14")} data-testid="fair-select">
              <option value="">off</option><option value="3">unseen 3 days</option><option value="7">unseen 7 days</option><option value="14">unseen 14 days</option></select></label>
        </div>
        <div className="overflow-x-auto"><table className="w-full min-w-[560px]" data-testid="budget-table">
          <thead><tr className="text-left text-xs text-muted-foreground"><th className="py-1 font-medium">Policy</th><th className="font-medium">Events per review</th><th className="font-medium">Agitation followed up</th><th className="font-medium">BP/pulse anticipated</th><th className="font-medium">Never reviewed</th></tr></thead>
          <tbody>{rows.map((x) => (
            <tr key={x.name} className={`border-t ${x.echos ? "bg-accent font-semibold" : ""}`}>
              <td className="py-1.5">{x.name}</td>
              <td><span className="flex items-center gap-2"><span className="h-2.5 w-24 overflow-hidden rounded bg-muted" aria-hidden="true"><span className="block h-full" style={{ width: `${(100 * x.r.events_per_review) / max}%`, background: x.echos ? "var(--chart-2)" : "var(--chart-1)" }} /></span><span className="num">{x.r.events_per_review.toFixed(3)}</span></span></td>
              <td className="num">{(x.r.agitation_followed_up * 100).toFixed(0)}%</td><td className="num">{(x.r.vitals_anticipated * 100).toFixed(0)}%</td><td className="num">{Math.round(x.r.patients_never_reviewed)} of 56</td>
            </tr>))}</tbody>
        </table></div>
        <p className="text-muted-foreground">Read it like this: the BP-limit rule is as good as ECHOS on total events but leaves agitation almost untouched; the fairness floor trades a little yield for many fewer people who are never seen. The right setting is a clinical decision, not a number the model can choose.</p>
      </CardContent></Card>)
}

export default function TestYourself() {
  const [sp, setSp] = useSearchParams()
  const tab = (TABS.find((t) => t.id === sp.get("tab")) ?? TABS[0]).id
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">Test it yourself</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">Three small experiments on real TIHM data. Everything is a historical test: the system only sees data up to each day, and what happened next is revealed afterwards.</p>
      </div>
      <div role="tablist" aria-label="Experiments" className="flex flex-wrap gap-2">
        {TABS.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setSp({ tab: t.id })} data-testid={`tab-${t.id}`}
          className={`rounded-md border px-3 py-1.5 text-sm font-medium ${tab === t.id ? "bg-accent text-accent-foreground" : "bg-card"}`}>{t.label}</button>)}
      </div>
      {tab === "audit" && <Audit />}{tab === "game" && <Game />}{tab === "budget" && <BudgetView />}
    </div>
  )
}
