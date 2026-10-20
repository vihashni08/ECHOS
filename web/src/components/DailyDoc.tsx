import { AlertTriangle, ArrowDown, ArrowUp, Check, ExternalLink, Info, Minus, ShieldAlert, User, Stethoscope } from "lucide-react"
import { cn } from "@/lib/utils"
import { StatusBadge } from "@/lib/status"

/** The structured daily report served as `doc` by GET /api/patients/{pid}/reports/daily (echos/reports/daily/document.py). */
export interface DocItem { text: string; cites?: string[]; ref_numbers?: number[] }
export interface DocAction extends DocItem { id: string; priority: string; priority_label: string; owner: string; owner_label: string; escalate_if?: string }
export interface DocRow { key: string; name: string; unit?: string; value: number | null; usual: number | null; diff: number | null; status?: string; note: string }
export interface DocRef { n: number; id: string; origin?: string | null; title?: string | null; publisher?: string | null; year?: string | null; page?: string | null; url?: string | null; excerpt?: string | null }
export interface Doc {
  header: { patient_id: string; day: string; status: string; status_label: string; mode: string; generated: string; model?: string | null }
  situation: string; background: string[]; alerts: string[]; trend: { day: string; status: string }[]
  vitals: DocRow[]; case?: { headline: string; evidence_level: string; next_checks: string[] } | null
  assessment: DocItem[]; changes: DocItem[]; actions: DocAction[]; guidance: DocItem[]; references: DocRef[]
  data_note?: string | null; disclaimer: string
}

const num = (x: number | null | undefined, unit = "") => (x === null || x === undefined ? "–" : `${Math.round(x * 10) / 10}${unit ? ` ${unit}` : ""}`)
const signed = (d: number | null) => (d === null ? "–" : d === 0 ? "0" : `${d > 0 ? "+" : "−"}${Math.abs(d)}`)

const PRIORITY: Record<string, string> = {
  NOW: "border-urgent-line bg-urgent-bg text-urgent-fg", TODAY: "border-monitor-line bg-monitor-bg text-monitor-fg",
  WITHIN_48H: "border-border bg-secondary text-foreground", THIS_WEEK: "border-border bg-secondary text-muted-foreground",
}

function Cites({ nums, onRef }: { nums?: number[]; onRef: (n: number) => void }) {
  if (!nums?.length) return null
  return <sup className="ml-1 whitespace-nowrap">{nums.map((n) => (
    <button key={n} onClick={() => onRef(n)} className="num px-0.5 text-primary underline" aria-label={`Reference ${n}`}>[{n}]</button>))}</sup>
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return <section aria-labelledby={id} className="flex flex-col gap-2"><h2 id={id} className="border-b pb-1 text-base font-semibold">{title}</h2>{children}</section>
}

function Table({ caption, rows, first }: { caption: string; rows: DocRow[]; first: string }) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full min-w-[34rem] text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-secondary text-left text-xs uppercase tracking-wide text-muted-foreground">
          <tr><th scope="col" className="px-3 py-2">{first}</th><th scope="col" className="px-3 py-2 text-right">Today</th><th scope="col" className="px-3 py-2 text-right">Usual</th>
            <th scope="col" className="px-3 py-2 text-right">Difference</th><th scope="col" className="px-3 py-2">Status</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const out = r.status === "HIGH" || r.status === "LOW"
            const Icon = r.status === "HIGH" ? ArrowUp : r.status === "LOW" ? ArrowDown : r.status === "WITHIN" ? Check : Minus
            return (
              <tr key={r.key} className={cn("border-t", out && "bg-monitor-bg/60")}>
                <th scope="row" className="px-3 py-2 text-left font-medium">{r.name}</th>
                <td className="num px-3 py-2 text-right">{num(r.value, r.unit)}</td>
                <td className="num px-3 py-2 text-right text-muted-foreground">{num(r.usual, r.unit)}</td>
                <td className="num px-3 py-2 text-right">{signed(r.diff)}</td>
                <td className="px-3 py-2"><span className={cn("inline-flex items-center gap-1", out && "font-semibold text-monitor-fg")}><Icon className="size-3.5 shrink-0" aria-hidden="true" />{r.note}</span></td>
              </tr>)
          })}
        </tbody>
      </table>
    </div>
  )
}

export default function DailyDoc({ doc, onSource }: { doc: Doc; onSource: (id: string) => void }) {
  const h = doc.header
  const refById = (n: number) => doc.references.find((r) => r.n === n)
  const openRef = (n: number) => { const r = refById(n); if (r) { document.getElementById(`ref-${n}`)?.scrollIntoView({ behavior: "smooth", block: "center" }) } }
  const byPriority = ["NOW", "TODAY", "WITHIN_48H", "THIS_WEEK"].map((p) => ({ p, items: doc.actions.filter((a) => a.priority === p) })).filter((g) => g.items.length)
  return (
    <article className="flex flex-col gap-5 text-sm" data-testid="daily-doc">
      <header className="flex flex-wrap items-start justify-between gap-3 rounded-lg border bg-secondary/40 px-4 py-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-xs uppercase tracking-wide text-muted-foreground">Patient (de-identified) · Report date</span>
          <span className="num font-semibold">{h.patient_id} · {h.day}</span>
          <span className="text-xs text-muted-foreground">Generated {h.generated} · {h.mode === "llm" ? <>AI-assisted wording{h.model ? <> (<span className="num">{h.model}</span>)</> : ""}, every sentence checked by rules</> : "Rule-based template"}</span>
        </div>
        <StatusBadge status={h.status} className="text-base" />
      </header>

      <Section id="sit" title="Situation"><p>{doc.situation}</p>
        {doc.trend.length > 1 && (
          <ol className="flex flex-wrap gap-1.5" aria-label="Last days" data-testid="doc-trend">{doc.trend.map((t) => (
            <li key={t.day} className="flex items-center gap-1 text-xs"><span className="num text-muted-foreground">{t.day.slice(5)}</span><StatusBadge status={t.status} className="px-1.5 py-0 text-[11px]" /></li>))}</ol>)}
      </Section>

      {doc.vitals.length > 0 && <Section id="vit" title="Vital signs against this person's own usual"><Table caption="Vital signs today against this person's usual" rows={doc.vitals} first="Measure" />
        <p className="text-xs text-muted-foreground">{doc.background[0]}</p></Section>}

      {doc.assessment.length > 0 && <Section id="ass" title="Assessment">
        {doc.assessment.map((a, i) => <p key={i}>{a.text}<Cites nums={a.ref_numbers} onRef={openRef} /></p>)}
        {doc.case && <p className="flex items-start gap-1.5 text-xs text-muted-foreground"><Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />Case file: {doc.case.headline} Evidence level: {doc.case.evidence_level.toLowerCase()} (a rule-based count of how many independent signals agree).</p>}
      </Section>}

      <Section id="chg" title="What changed compared with their usual pattern">
        <ul className="list-disc space-y-1.5 pl-5">{doc.changes.map((c, i) => <li key={i}>{c.text}<Cites nums={c.ref_numbers} onRef={openRef} /></li>)}</ul></Section>

      <Section id="plan" title="Plan">
        <div className="flex flex-col gap-3" data-testid="doc-plan">
          {byPriority.map((g) => (
            <div key={g.p} className="flex flex-col gap-2">
              {g.items.map((a) => {
                const Owner = a.owner === "CLINICIAN" ? Stethoscope : User
                return (
                  <div key={a.id} className="rounded-lg border bg-card px-3 py-2.5" data-testid="doc-action">
                    <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs font-semibold">
                      <span className="num text-muted-foreground">{a.id}</span>
                      <span className={cn("rounded-full border px-2 py-0.5", PRIORITY[a.priority])}>{a.priority_label}</span>
                      <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5"><Owner className="size-3.5" aria-hidden="true" />{a.owner_label}</span>
                    </div>
                    <p>{a.text}<Cites nums={a.ref_numbers} onRef={openRef} /></p>
                    {a.escalate_if && <p className="mt-2 flex items-start gap-1.5 rounded-md border border-urgent-line bg-urgent-bg px-2.5 py-1.5 text-urgent-fg">
                      <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" /><span><strong>Escalate if:</strong> {a.escalate_if}</span></p>}
                  </div>)
              })}
            </div>))}
        </div>
      </Section>

      {doc.guidance.length > 0 && <Section id="gd" title="Guidance for the care team">
        <ul className="list-disc space-y-1.5 pl-5">{doc.guidance.map((g, i) => <li key={i}>{g.text}<Cites nums={g.ref_numbers} onRef={openRef} /></li>)}</ul></Section>}

      {h.status !== "NORMAL" && <Section id="refs" title="References">
        {doc.references.length === 0 ? <p className="flex items-start gap-1.5 text-muted-foreground"><AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />No supporting source was retrieved for these findings.</p> : (
          <ol className="flex flex-col gap-2" data-testid="doc-refs">{doc.references.map((r) => (
            <li key={r.n} id={`ref-${r.n}`} className="grid grid-cols-[1.75rem_1fr] gap-1 scroll-mt-24">
              <span className="num text-muted-foreground">{r.n}.</span>
              <span><span className="font-medium">{r.title}</span>. {r.publisher}{r.year ? `, ${r.year}` : ""}{r.page ? `, page ${r.page}` : ""}.{" "}
                <span className="num text-xs text-muted-foreground">[{r.id}]</span>{" "}
                {r.url ? <a href={r.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5 text-primary underline">PubMed<ExternalLink className="size-3" aria-hidden="true" /></a>
                  : <button onClick={() => onSource(r.id)} className="text-primary underline">Show excerpt</button>}
                {r.excerpt && <span className="mt-0.5 block text-xs italic text-muted-foreground">“{r.excerpt}”</span>}</span>
            </li>))}</ol>)}
      </Section>}

      {doc.data_note && <Section id="dn" title="Data note"><p className="flex items-start gap-1.5 text-muted-foreground"><Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />{doc.data_note}</p></Section>}
      <footer className="border-t pt-2 text-xs text-muted-foreground">{doc.disclaimer}</footer>
    </article>
  )
}
