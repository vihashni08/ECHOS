import type { ReactNode } from "react"
import { Inbox } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed px-4 py-8 text-center">
      <Inbox className="size-6 text-muted-foreground" aria-hidden="true" />
      <p className="font-medium">{title}</p>
      {children && <p className="max-w-md text-sm text-muted-foreground">{children}</p>}
    </div>
  )
}
export const CardSkeleton = ({ h = "h-32" }: { h?: string }) => <Skeleton className={`${h} w-full`} role="status" aria-label="Loading" />
export function ErrorNote({ error }: { error: unknown }) {
  return <p role="alert" className="rounded-md border border-urgent-line bg-urgent-bg px-3 py-2 text-sm text-urgent-fg">{error instanceof Error ? error.message : "Something went wrong"}</p>
}
