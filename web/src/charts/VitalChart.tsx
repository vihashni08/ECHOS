import { AlertTriangle } from "lucide-react"
import { Area, CartesianGrid, ComposedChart, Legend, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts"
import type { Schemas } from "@/lib/api"

type Series = Schemas["VitalSeries"]
const DAY = 86400000
const ts = (s: string) => new Date(s.replace(" ", "T")).getTime()
const fmtX = (v: number) => new Date(v).toISOString().slice(5, 10)

function FlagShape(p: { cx?: number; cy?: number }) {
  const { cx = 0, cy = 0 } = p
  return <path d={`M${cx} ${cy - 8} L${cx + 8} ${cy} L${cx} ${cy + 8} L${cx - 8} ${cy} Z`} fill="var(--urgent-bg)" stroke="var(--urgent-line)" strokeWidth={2} />
}
function Tip({ active, payload, unit }: { active?: boolean; payload?: { payload: { x: number; y: number; flagged?: boolean } }[]; unit: string }) {
  const p = payload?.find((q) => q.payload.y !== undefined)?.payload
  if (!active || !p) return null
  return <div className="rounded-md border bg-popover px-2.5 py-1.5 text-xs text-popover-foreground shadow"><div className="num">{new Date(p.x).toISOString().slice(0, 16).replace("T", " ")}</div>
    <div className="num font-semibold">{p.y} {unit}{p.flagged ? " (outside personal band)" : ""}</div></div>
}

/** One vital (or a pair, e.g. systolic + diastolic) against the past-only personal band; flagged readings get a diamond marker. */
export default function VitalChart({ title, unit, series, start, end }: { title: string; unit: string; series: { s: Series; color: string; name: string }[]; start: string; end: string }) {
  const x0 = new Date(start).getTime(), x1 = new Date(end).getTime() + DAY
  const established = series.some((q) => q.s.baseline_established)
  const nPts = series.reduce((n, q) => n + q.s.points.length, 0)
  const bands = series.map((q) => q.s.bands.flatMap((b) => [{ x: new Date(b.day).getTime(), band: [b.lo, b.hi] }, { x: new Date(b.day).getTime() + DAY - 1, band: [b.lo, b.hi] }]))
  const allY = [...series.flatMap((q) => q.s.points.map((p) => p.value)), ...series.flatMap((q) => q.s.bands.flatMap((b) => [b.lo, b.hi]))]
  const pad = allY.length ? Math.max((Math.max(...allY) - Math.min(...allY)) * 0.12, 1) : 1
  const yDomain: [number, number] = allY.length ? [Math.floor(Math.min(...allY) - pad), Math.ceil(Math.max(...allY) + pad)] : [0, 1]
  const summary = series.map((q) => {
    const v = q.s.points.map((p) => p.value)
    return v.length ? `${q.name}: ${v.length} readings, ${Math.min(...v)} to ${Math.max(...v)} ${unit}, ${q.s.points.filter((p) => p.flagged).length} outside the personal band` : `${q.name}: no readings`
  }).join(". ")
  return (
    <figure className="flex flex-col gap-1.5 rounded-lg border bg-card p-3" aria-label={`${title} chart`}>
      <figcaption className="flex items-center justify-between text-sm font-semibold">
        {title}
        {!established && <span className="text-xs font-normal text-muted-foreground">personal baseline not yet established</span>}
      </figcaption>
      {nPts === 0 ? <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">No readings in this period</div> : (
        <div className="h-56 w-full" role="img" aria-label={summary}>
          <ResponsiveContainer>
            <ComposedChart margin={{ top: 6, right: 8, bottom: 18, left: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis type="number" dataKey="x" domain={[x0, x1]} scale="time" tickFormatter={fmtX} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} label={{ value: "Date (month-day)", position: "insideBottom", offset: -12, fontSize: 11, fill: "var(--muted-foreground)" }} />
              <YAxis type="number" dataKey="y" domain={yDomain} allowDataOverflow width={42} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} label={{ value: unit, angle: -90, position: "insideLeft", fontSize: 11, fill: "var(--muted-foreground)" }} />
              <Tooltip content={<Tip unit={unit} />} />
              <Legend verticalAlign="top" height={22} iconSize={10} wrapperStyle={{ fontSize: 11 }} />
              {series.map((q, i) => bands[i].length > 0 && <Area key={`b${i}`} data={bands[i]} dataKey="band" xAxisId={0} yAxisId={0} name={`${q.name} personal band`} stroke="none" fill={q.color} fillOpacity={0.16} isAnimationActive={false} legendType="none" />)}
              {series.map((q) => <Scatter key={q.name} name={q.name} data={q.s.points.filter((p) => !p.flagged).map((p) => ({ x: ts(p.t), y: p.value }))} fill={q.color} isAnimationActive={false} />)}
              {series.some((q) => q.s.points.some((p) => p.flagged)) && (
                <Scatter name="Flagged (outside band)" legendType="diamond" shape={<FlagShape />} isAnimationActive={false}
                  data={series.flatMap((q) => q.s.points.filter((p) => p.flagged).map((p) => ({ x: ts(p.t), y: p.value, flagged: true })))} fill="var(--urgent-line)" />)}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className="text-xs text-muted-foreground">Shaded band: this person's usual range (past readings only).</p>
      <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
        {series.some((q) => q.s.points.some((p) => p.flagged)) && <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />}{summary}
      </p>
    </figure>
  )
}
