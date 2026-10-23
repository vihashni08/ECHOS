import { BookOpenText, ShieldCheck, Sparkles } from "lucide-react"
import type { EvidenceCardT } from "@/lib/queries"

/** The Evidence agent's note for one case: a short explanation, the guideline passages it cites, and how it was produced (validated Gemini text or fixed template). */
export default function EvidenceCard({ card, compact }: { card?: EvidenceCardT; compact?: boolean }) {
  if (!card) return <p className="text-xs text-muted-foreground" data-testid="evidence-pending">Evidence agent: waiting for this case…</p>
  const gem = card.source === "gemini"
  const cited = card.passages.filter((p) => card.citations.includes(p.id))
  const shown = cited.length ? cited : card.passages.slice(0, 1)
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border bg-card p-2.5 text-sm" data-testid="evidence-card">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="inline-flex items-center gap-1 font-semibold"><BookOpenText className="size-3.5" aria-hidden="true" />Evidence</span>
        {gem
          ? <span className="inline-flex items-center gap-1 rounded-full border border-normal-line bg-normal-bg px-2 py-0.5 font-semibold text-normal-fg" title={`Written by ${card.model}; every check passed`}><Sparkles className="size-3" aria-hidden="true" />Gemini, checked</span>
          : <span className="inline-flex items-center gap-1 rounded-full border border-gap-line bg-gap-bg px-2 py-0.5 font-semibold text-gap-fg" title={card.validator.problems?.length ? `Template used: ${card.validator.problems.join("; ")}` : "Fixed template"}><ShieldCheck className="size-3" aria-hidden="true" />Template</span>}
      </div>
      <p>{card.explanation}</p>
      {!compact && shown.map((p) => (
        <details key={p.id} className="rounded-md border bg-muted/40 px-2 py-1 text-xs">
          <summary className="cursor-pointer font-medium">{p.source}, p.{p.page} <span className="num text-muted-foreground">[{p.id}]</span></summary>
          <p className="mt-1 text-muted-foreground">{p.text}</p>
          {p.url && <a className="underline" href={p.url} target="_blank" rel="noreferrer">Open the guideline</a>}
        </details>))}
      {!card.passages.length && <p className="text-xs text-muted-foreground">No guideline in the local index covers this reason, so nothing is cited.</p>}
    </div>
  )
}
