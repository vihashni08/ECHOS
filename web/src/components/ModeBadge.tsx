import { Info } from "lucide-react"
import { MODE, useMode } from "@/lib/live"
import { cn } from "@/lib/utils"

/** Which kind of data the whole page shows: always visible, never colour alone. */
export function ModeBadge({ className }: { className?: string }) {
  const { mode } = useMode()
  const m = MODE[mode]
  return <span title={m.what} data-testid="mode-badge" className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-bold tracking-wide", m.cls, className)}>{m.label}</span>
}

export function ModeBanner() {
  const { mode } = useMode()
  if (mode === "live") return null
  const m = MODE[mode]
  return (
    <div role="note" data-testid="mode-banner" className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2 text-sm", m.cls)}>
      <Info className="size-4 shrink-0" aria-hidden="true" />
      <span><strong>{m.label}:</strong> {m.what}</span>
    </div>
  )
}
