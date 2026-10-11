import { ExternalLink } from "lucide-react"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { ErrorNote } from "./States"
import { useSource } from "@/lib/queries"

/** Side panel for one cited source: title, source, year, link and (local guidelines only) a short excerpt. */
export default function SourceSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useSource(id ?? undefined)
  const s = q.data
  return (
    <Sheet open={id !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md" data-testid="source-panel">
        <SheetHeader>
          <SheetTitle>Source <span className="num">{id}</span></SheetTitle>
          <SheetDescription>For clinician review. Not a diagnosis.</SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-3 px-4 pb-6 text-sm">
          {q.isLoading ? <Skeleton className="h-32 w-full" /> : q.isError ? <ErrorNote error={q.error} /> : s && (
            <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-2">
              <dt className="text-muted-foreground">Title</dt><dd className="font-medium">{s.title}</dd>
              <dt className="text-muted-foreground">{s.origin === "pubmed" ? "Journal" : "Source"}</dt><dd>{s.origin === "pubmed" ? s.journal ?? "n/a" : s.source}</dd>
              <dt className="text-muted-foreground">Year</dt><dd className="num">{s.year ?? "n/a"}</dd>
              {s.page && <><dt className="text-muted-foreground">Page</dt><dd className="num">{s.page}</dd></>}
              <dt className="text-muted-foreground">Link</dt>
              <dd>{s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-primary underline">{s.origin === "pubmed" ? `PubMed ${id?.replace("PM-", "PMID ")}` : "Open"}<ExternalLink className="size-3.5" aria-hidden="true" /></a> : "no link available"}</dd>
              {s.excerpt && <><dt className="text-muted-foreground">Excerpt</dt><dd><blockquote className="border-l-2 border-primary pl-3 italic">{s.excerpt}</blockquote></dd></>}
            </dl>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
