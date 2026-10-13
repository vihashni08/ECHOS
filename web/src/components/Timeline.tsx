import { Link } from "react-router-dom"
import { useStatuses } from "@/lib/queries"
import { STATUS, type StatusKey } from "@/lib/status"
import { cn } from "@/lib/utils"

/** Last N days as coloured blocks WITH a letter: N normal, M monitor, U urgent (never colour alone). */
export default function Timeline({ pid, days = 14, link = false }: { pid: string; days?: number; link?: boolean }) {
  const st = useStatuses(pid, days)
  const rows = (st.data ?? []).slice(-days)
  if (!rows.length) return <span className="text-sm text-muted-foreground">no days yet</span>
  const L: Record<string, string> = { NORMAL: "N", MONITOR: "M", URGENT: "U" }
  const body = (
    <ol className="flex gap-0.5" aria-label={`Last ${rows.length} days`} data-testid="timeline">
      {rows.map((d) => {
        const s = STATUS[(d.status as StatusKey)] ?? STATUS.DATA_GAP
        return <li key={d.day} title={`${d.day}: ${s.label}`} className={cn("num flex h-6 w-6 items-center justify-center rounded border text-[11px] font-bold", s.cls)}>{L[d.status] ?? "?"}</li>
      })}
    </ol>
  )
  return link ? <Link to={`/patients/${pid}`} aria-label={`Open patient ${pid}`}>{body}</Link> : body
}
