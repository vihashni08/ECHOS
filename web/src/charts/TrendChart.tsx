import { Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts"

export type Row = { day: string } & Record<string, number | string | null | undefined>
export type SeriesSpec = { key: string; name: string; color: string; dashed?: boolean; kind?: "line" | "bar" }

const fmtDay = (d: string) => d.slice(5)

function Tip({ active, label, payload, unit, fmt }: { active?: boolean; label?: string; payload?: { name: string; value: number; color: string; dataKey: string }[]; unit: string; fmt: (v: number) => string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border bg-popover px-2.5 py-1.5 text-xs text-popover-foreground shadow">
      <div className="num mb-0.5 font-medium">{label}</div>
      {payload.filter((p) => p.value != null && !p.dataKey.startsWith("flag")).map((p) => (
        <div key={p.dataKey} className="num flex items-center gap-1.5"><span className="inline-block h-0.5 w-3" style={{ background: p.color }} aria-hidden="true" />{p.name}: <span className="font-semibold">{fmt(p.value)}{unit ? ` ${unit}` : ""}</span></div>
      ))}
      {payload.some((p) => p.dataKey.startsWith("flag") && p.value != null) && <div className="mt-0.5 font-semibold text-urgent-fg">Red-flag reading (urgent alert)</div>}
    </div>
  )
}

/** One measure over days on ONE y-axis (never two scales). Dashed series = the person's usual. `flagKey` marks red-flag days with a labelled diamond. */
export default function TrendChart({ title, unit, rows, series, flagKey, refLine, fmt = (v) => String(Math.round(v * 10) / 10), domain, summary, height = "h-52" }: {
  title: string; unit: string; rows: Row[]; series: SeriesSpec[]; flagKey?: string; refLine?: { y: number; label: string }; fmt?: (v: number) => string
  domain?: [number | "auto", number | "auto"]; summary: string; height?: string
}) {
  const hasData = rows.some((r) => series.some((s) => r[s.key] != null))
  const flagged = flagKey ? rows.filter((r) => r[flagKey] != null).length : 0
  return (
    <figure className="flex flex-col gap-1.5 rounded-lg border bg-card p-3" aria-label={`${title} chart`}>
      <figcaption className="text-sm font-semibold">{title}</figcaption>
      {!hasData ? <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">No data yet</div> : (
        <div className={`${height} w-full`} role="img" aria-label={summary}>
          <ResponsiveContainer>
            <ComposedChart data={rows} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey="day" tickFormatter={fmtDay} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} minTickGap={16} />
              <YAxis domain={domain ?? ["auto", "auto"]} width={40} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} tickFormatter={(v) => fmt(v as number)} />
              <Tooltip content={<Tip unit={unit} fmt={fmt} />} cursor={{ stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }} />
              {(series.length > 1 || flagged > 0) && <Legend verticalAlign="top" height={22} iconSize={10} wrapperStyle={{ fontSize: 11 }} />}
              {refLine && <ReferenceLine y={refLine.y} stroke="var(--muted-foreground)" strokeDasharray="4 4" label={{ value: refLine.label, position: "insideTopRight", fontSize: 10, fill: "var(--muted-foreground)" }} />}
              {series.map((s) => s.kind === "bar"
                ? <Bar key={s.key} dataKey={s.key} name={s.name} fill={s.color} radius={[4, 4, 0, 0]} maxBarSize={14} isAnimationActive={false} />
                : <Line key={s.key} dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2} strokeDasharray={s.dashed ? "5 4" : undefined}
                    dot={s.dashed ? false : { r: 3, strokeWidth: 0, fill: s.color }} activeDot={{ r: 5 }} connectNulls isAnimationActive={false} />)}
              {flagKey && flagged > 0 && <Scatter dataKey={flagKey} name="Red-flag reading" legendType="diamond" fill="var(--urgent-line)" shape="diamond" isAnimationActive={false} />}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className="text-xs text-muted-foreground">{summary}</p>
    </figure>
  )
}
