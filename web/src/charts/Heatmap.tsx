import type { Schemas } from "@/lib/api"

/** Room x hour-of-day heatmap with a numeric legend. Cells carry their count as text for screen readers. */
export default function Heatmap({ r }: { r: Schemas["RoutineOut"] }) {
  const max = Math.max(1, ...r.counts.flat())
  const total = r.counts.flat().reduce((a, b) => a + b, 0)
  const busiest = r.rooms.map((room, i) => [room, r.counts[i].reduce((a, b) => a + b, 0)] as const).sort((a, b) => b[1] - a[1])[0]
  return (
    <figure className="flex flex-col gap-2 rounded-lg border bg-card p-3" aria-label="Room by hour heatmap">
      <figcaption className="text-sm font-semibold">Where and when: room activity by hour of day</figcaption>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[36rem] table-fixed border-separate border-spacing-0.5 text-[11px]" role="img" aria-label={`Sensor events by room and hour, ${total} in total${total ? `, most in ${busiest[0]} (${busiest[1]})` : ""}`}>
          <thead><tr><th className="w-24" />{r.hours.map((h) => <th key={h} className="num overflow-visible whitespace-nowrap font-normal text-muted-foreground">{h % 3 === 0 ? String(h).padStart(2, "0") : ""}</th>)}</tr></thead>
          <tbody>
            {r.rooms.map((room, i) => (
              <tr key={room}>
                <th scope="row" className="pr-2 text-right text-xs font-normal">{room}</th>
                {r.hours.map((h) => {
                  const v = r.counts[i][h]
                  return <td key={h} title={`${room}, ${String(h).padStart(2, "0")}:00: ${v} events`} className="h-5 rounded-sm" style={{ background: v ? `color-mix(in srgb, var(--primary) ${Math.round(12 + 88 * (v / max))}%, var(--card))` : "var(--muted)" }}><span className="sr-only">{v}</span></td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground" aria-label="Legend">
        <span>Hour of day (00 to 23) →</span><span className="ml-auto num">0</span>
        <span className="h-3 w-28 rounded" style={{ background: "linear-gradient(to right, var(--muted), var(--primary))" }} aria-hidden="true" />
        <span className="num">{max} events</span>
      </div>
      <p className="text-xs text-muted-foreground">{total} sensor events in this period{total ? `; the busiest room is ${busiest[0]} (${busiest[1]} events)` : ""}.</p>
    </figure>
  )
}
