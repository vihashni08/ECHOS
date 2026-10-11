import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis, Area } from "recharts"
import type { Schemas } from "@/lib/api"

/** One routine measure per day (bars) against the personal band (needs >= 7 earlier days). */
export default function BandBars({ title, unit, k, days, note }: { title: string; unit: string; k: string; days: Schemas["RoutineDay"][]; note: string }) {
  const data = days.map((d) => { const m = d.metrics[k]; return { day: d.day.slice(5), value: m.value ?? null, band: m.lo != null && m.hi != null ? [Math.max(0, m.lo), m.hi] : null, base: m.base ?? null, z: m.z } })
  const est = data.some((d) => d.band)
  const vals = data.map((d) => d.value).filter((v): v is number => v != null)
  const outside = days.filter((d) => { const m = d.metrics[k]; return m.value != null && m.hi != null && m.lo != null && (m.value > m.hi || m.value < m.lo) }).length
  const summary = vals.length ? `${title}: ${vals.length} days with data, ${Math.min(...vals)} to ${Math.max(...vals)} ${unit}${est ? `, ${outside} day(s) outside the personal band` : ""}.` : `${title}: no activity data in this period.`
  return (
    <figure className="flex flex-col gap-1.5 rounded-lg border bg-card p-3" aria-label={`${title} chart`}>
      <figcaption className="flex items-center justify-between text-sm font-semibold">{title}{!est && <span className="text-xs font-normal text-muted-foreground">{note}</span>}</figcaption>
      <div className="h-56 w-full" role="img" aria-label={summary}>
        <ResponsiveContainer>
          <ComposedChart data={data} margin={{ top: 6, right: 8, bottom: 18, left: 0 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
            <XAxis dataKey="day" tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} label={{ value: "Date (month-day)", position: "insideBottom", offset: -12, fontSize: 11, fill: "var(--muted-foreground)" }} />
            <YAxis width={42} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} label={{ value: unit, angle: -90, position: "insideLeft", fontSize: 11, fill: "var(--muted-foreground)" }} />
            <Tooltip formatter={(v) => (Array.isArray(v) ? `${v[0]} to ${v[1]}` : v)} contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", fontSize: 12 }} />
            <Legend verticalAlign="top" height={22} iconSize={10} wrapperStyle={{ fontSize: 11 }} />
            {est && <Area dataKey="band" name="Personal band" stroke="none" fill="var(--chart-1)" fillOpacity={0.16} isAnimationActive={false} />}
            <Bar dataKey="value" name={title} fill="var(--chart-3)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
            {est && <Line dataKey="base" name="Personal baseline" stroke="var(--chart-1)" strokeDasharray="4 3" dot={false} isAnimationActive={false} />}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="text-xs text-muted-foreground">{summary}</p>
    </figure>
  )
}
