import { AlertOctagon, CheckCircle2, CircleSlash, ClipboardCheck, Eye, Hourglass, type LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"

export type StatusKey = "URGENT" | "REVIEW" | "WATCH" | "NORMAL" | "PENDING" | "DATA_GAP"
export const STATUS: Record<StatusKey, { label: string; hint: string; icon: LucideIcon; cls: string }> = {
  URGENT: { label: "Urgent", hint: "A red-flag reading: an urgent alert went to the caregiver and the clinician", icon: AlertOctagon, cls: "bg-urgent-bg text-urgent-fg border-urgent-line" },
  REVIEW: { label: "Review today", hint: "On today's clinician review list (Review Coordinator)", icon: ClipboardCheck, cls: "bg-monitor-bg text-monitor-fg border-monitor-line" },
  WATCH: { label: "Watch", hint: "Above their usual, but not among today's reviews", icon: Eye, cls: "bg-gap-bg text-gap-fg border-gap-line" },
  NORMAL: { label: "Normal", hint: "Nothing unusual compared with this person's own pattern", icon: CheckCircle2, cls: "bg-normal-bg text-normal-fg border-normal-line" },
  PENDING: { label: "Scoring", hint: "Scored by the Patient Monitor, waiting for the day's review decision", icon: Hourglass, cls: "bg-gap-bg text-gap-fg border-gap-line" },
  DATA_GAP: { label: "No data", hint: "No readings and no activity for this day", icon: CircleSlash, cls: "bg-gap-bg text-gap-fg border-gap-line" },
}

/** Status is always icon + text label (never colour alone). */
export function StatusBadge({ status, className }: { status?: string | null; className?: string }) {
  const s = STATUS[(status as StatusKey) ?? "DATA_GAP"] ?? STATUS.DATA_GAP
  const Icon = s.icon
  return (
    <span title={s.hint} className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-sm font-semibold whitespace-nowrap", s.cls, className)}>
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      <span>{status ? s.label : "No data"}</span>
    </span>
  )
}
