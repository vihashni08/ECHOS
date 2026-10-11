import { ChevronRight } from "lucide-react"
import type { Schemas } from "@/lib/api"

/** 'How this report was made': model or template, tools called, sources used, validator result, fallback reason. */
export default function Provenance({ p, mode, onSource }: { p?: Schemas["Provenance"] | null; mode: string; onSource: (id: string) => void }) {
  const model = p?.model && p.model !== "template" ? p.model : null
  return (
    <details className="group rounded-lg border bg-card" data-testid="provenance">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold">
        <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden="true" />How this report was made
      </summary>
      <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-2 border-t px-4 py-3 text-sm">
        <dt className="text-muted-foreground">Written by</dt><dd>{model ? <>AI model <span className="num">{model}</span>{p?.cache_hit ? " (reused from cache)" : ""}</> : "Fixed template (no AI model)"}</dd>
        <dt className="text-muted-foreground">Mode</dt><dd>{mode}</dd>
        <dt className="text-muted-foreground">Tools called</dt>
        <dd>{p?.tools?.length ? <ul className="list-disc pl-4">{p.tools.map((t, i) => <li key={i}><span className="num">{String(t.name)}</span>{t.result_summary ? `: ${String(t.result_summary)}` : ""}</li>)}</ul> : "none"}</dd>
        <dt className="text-muted-foreground">Sources used</dt>
        <dd>{p?.sources_retrieved?.length ? <ul className="flex flex-wrap gap-2">{p.sources_retrieved.map((s) => {
          const id = String(s.id)
          return <li key={id}><button onClick={() => onSource(id)} className="num rounded border px-1.5 py-0.5 text-primary underline hover:bg-accent" aria-label={`Open source ${id}`}>{id}</button></li>
        })}</ul> : "none"}</dd>
        <dt className="text-muted-foreground">Checked by</dt>
        <dd>{p?.validator ? (p.validator.ok ? "Automatic validator: passed" : `Automatic validator: ${(p.validator.errors as string[] | undefined)?.join("; ") ?? "failed"}`) : "n/a"}</dd>
        {p?.fallback_reason && <><dt className="text-muted-foreground">Fallback</dt><dd>{p.fallback_reason}</dd></>}
      </dl>
    </details>
  )
}
