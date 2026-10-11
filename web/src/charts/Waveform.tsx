import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import type { Schemas } from "@/lib/api"

export default function Waveform({ w }: { w: Schemas["Waveform"] }) {
  const data = w.t_s.map((t, i) => ({ t, x: w.x_g[i], y: w.y_g[i], z: w.z_g[i], m: w.magnitude_g[i] }))
  const peak = Math.max(...w.magnitude_g)
  return (
    <figure className="flex flex-col gap-1.5" aria-label="Accelerometer waveform">
      <div className="h-72 w-full" role="img" aria-label={`3-axis accelerometer waveform of ${data.length} points; impact at ${w.impact_time_s} seconds with a peak of ${peak} g`}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 22, left: 4 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
            <XAxis dataKey="t" type="number" domain={[0, "dataMax"]} tickFormatter={(v) => Number(v).toFixed(1)} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} label={{ value: "Time (s)", position: "insideBottom", offset: -14, fontSize: 12, fill: "var(--muted-foreground)" }} />
            <YAxis width={44} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} label={{ value: "Acceleration (g)", angle: -90, position: "insideLeft", fontSize: 12, fill: "var(--muted-foreground)" }} />
            <Tooltip contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", fontSize: 12 }} formatter={(v) => `${v} g`} labelFormatter={(l) => `t = ${Number(l).toFixed(2)} s`} />
            <Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 12 }} />
            <ReferenceLine x={w.impact_time_s} stroke="var(--urgent-line)" strokeWidth={2} label={{ value: `Impact ${w.impact_time_s.toFixed(2)} s`, position: "insideTopRight", fill: "var(--urgent-fg)", fontSize: 12 }} />
            <Line dataKey="x" name="X axis" stroke="var(--chart-3)" dot={false} strokeWidth={1.4} isAnimationActive={false} />
            <Line dataKey="y" name="Y axis" stroke="var(--chart-2)" dot={false} strokeWidth={1.4} isAnimationActive={false} />
            <Line dataKey="z" name="Z axis" stroke="var(--chart-4)" dot={false} strokeWidth={1.4} isAnimationActive={false} />
            <Line dataKey="m" name="Magnitude" stroke="var(--chart-1)" dot={false} strokeWidth={2.4} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="text-xs text-muted-foreground">Simulated SisFall window, {w.fs_hz} Hz (shown every {w.downsample_stride}rd sample). The impact is at {w.impact_time_s.toFixed(2)} s, peak magnitude {peak.toFixed(1)} g.</p>
    </figure>
  )
}
