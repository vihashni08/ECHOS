import { Info } from "lucide-react"
import { cn } from "@/lib/utils"

const WHAT = "Real TIHM study data replayed day by day. Each person is scored by a model that never saw them; the day's confirmed outcomes come from the study record. Not live patients."

/** Which kind of data the whole page shows: always visible, never colour alone. */
export function ModeBadge({ className }: { className?: string }) {
  return <span title={WHAT} data-testid="mode-badge" className={cn("inline-flex items-center gap-1.5 rounded-full border border-gap-line bg-gap-bg px-2.5 py-0.5 text-xs font-bold tracking-wide text-gap-fg", className)}>REPLAY</span>
}

export function ModeBanner() {
  return (
    <div role="note" data-testid="mode-banner" className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-gap-line bg-gap-bg px-4 py-2 text-sm text-gap-fg">
      <Info className="size-4 shrink-0" aria-hidden="true" />
      <span><strong>REPLAY:</strong> {WHAT}</span>
    </div>
  )
}
