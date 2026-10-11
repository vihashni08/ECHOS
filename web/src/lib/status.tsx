import { AlertOctagon, AlertTriangle, CheckCircle2, CircleSlash, type LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"

export type StatusKey = "NORMAL" | "MONITOR" | "URGENT" | "DATA_GAP"
export const STATUS: Record<StatusKey, { label: string; hint: string; icon: LucideIcon; cls: string }> = {
  NORMAL: { label: "Normal", hint: "Nothing unusual compared with this person's own pattern", icon: CheckCircle2, cls: "bg-normal-bg text-normal-fg border-normal-line" },
  MONITOR: { label: "Monitor", hint: "Changes noticed: keep an eye on today", icon: AlertTriangle, cls: "bg-monitor-bg text-monitor-fg border-monitor-line" },
  URGENT: { label: "Urgent", hint: "Needs attention now", icon: AlertOctagon, cls: "bg-urgent-bg text-urgent-fg border-urgent-line" },
  DATA_GAP: { label: "Data gap", hint: "Not enough data to judge", icon: CircleSlash, cls: "bg-gap-bg text-gap-fg border-gap-line" },
}

/** Status is always icon + text label (never colour alone). */
export function StatusBadge({ status, className }: { status?: string | null; className?: string }) {
  const s = STATUS[(status as StatusKey) ?? "DATA_GAP"] ?? STATUS.DATA_GAP
  const Icon = s.icon
  const label = status ? s.label : "No data"
  return (
    <span title={s.hint} className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-sm font-semibold", s.cls, className)}>
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      <span>{label}</span>
    </span>
  )
}
