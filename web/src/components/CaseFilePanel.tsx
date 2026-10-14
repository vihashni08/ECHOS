import { Bot, CheckCircle2, ListChecks, Route, ShieldAlert } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { CardSkeleton } from "@/components/States"
import { useCase } from "@/lib/queries"
import { fmtDay } from "@/lib/format"
import { StatusBadge } from "@/lib/status"
import { cn } from "@/lib/utils"

const LEVEL: Record<string, { text: string; cls: string; hint: string }> = {
  STRONG: { text: "Strong evidence", cls: "bg-urgent-bg text-urgent-fg border-urgent-line", hint: "An absolute rule fired or two independent sources agree. Not a probability." },
  MODERATE: { text: "Moderate evidence", cls: "bg-monitor-bg text-monitor-fg border-monitor-line", hint: "One flag that repeats or persists. Not a probability." },
  LIMITED: { text: "Limited evidence", cls: "bg-gap-bg text-gap-fg border-gap-line", hint: "A single flag, or the data has caveats. Not a probability." },
}
const TOOL: Record<string, string> = { read_detectors: "Read what the detectors found", check_history: "Compared with the last days", check_agreement: "Checked whether sources agree", check_data_caveats: "Looked for data problems" }
const DET: Record<string, string> = { vitals: "Vitals detector", behaviour: "Behaviour detector", fall: "Fall detector" }

function stepText(t: { tool: string; result: unknown }): string {
  const r = t.result as Record<string, unknown>
  if (t.tool === "read_detectors") { const f = (r.flagged as string[]) ?? []; return f.length ? `Flagged by: ${f.map((x) => DET[x] ?? x).join(", ")}` : "No detector flagged anything; the case comes from the data checks" }
  if (t.tool === "check_history") return `${r.non_normal_days} changed day(s) in the last ${r.days_seen}; ${r.streak_before_today} in a row before today`
  if (t.tool === "check_agreement") return `${r.independent_sources} independent source(s) point the same way`
  if (t.tool === "check_data_caveats") { const c = (t.result as string[]) ?? []; return c.length ? c.join(" ") : "No data problems found" }
  return ""
}

/** "How this decision was made": the Case Investigator's file. Only changed or urgent days have one. */
export default function CaseFilePanel({ pid, day, status }: { pid: string; day?: string; status?: string | null }) {
  const c = useCase(pid, day)
  if (c.isLoading) return <CardSkeleton h="h-40" />
  if (!c.data) return <Card><CardContent className="pt-4 text-sm text-muted-foreground" data-testid="case-none">{status && status !== "NORMAL" ? "No case file was built for this day: the Case Investigator did not run on it (for example a replay made before it existed). The reasons above still come from the detectors." : "No case file: the latest day is normal, so the Case Investigator has nothing to investigate."}</CardContent></Card>
  const f = { ...c.data, next_checks: c.data.next_checks ?? [], caveats: c.data.caveats ?? [], trace: c.data.trace ?? [], reasons: c.data.reasons ?? [] }
  const lv = LEVEL[f.evidence_level] ?? LEVEL.LIMITED
  return (
    <Card data-testid="case-file">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2"><Bot className="size-5" aria-hidden="true" />Case file · <span className="num">{fmtDay(f.day)}</span></CardTitle>
        <div className="flex items-center gap-2"><StatusBadge status={f.status} /><span title={lv.hint} className={cn("rounded-full border px-2.5 py-0.5 text-xs font-semibold", lv.cls)} data-testid="evidence-level">{lv.text}</span></div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <p className="text-base font-medium" data-testid="case-headline">{f.headline}</p>
        {f.next_checks.length > 0 && (
          <div><h3 className="mb-1 flex items-center gap-1.5 font-semibold"><ListChecks className="size-4" aria-hidden="true" />Suggested next checks</h3>
            <ul className="list-disc space-y-1 pl-5">{f.next_checks.map((x, i) => <li key={i}>{x}</li>)}</ul></div>)}
        {f.caveats.length > 0 && (
          <div className="rounded-md border border-monitor-line bg-monitor-bg p-3 text-monitor-fg"><h3 className="mb-1 flex items-center gap-1.5 font-semibold"><ShieldAlert className="size-4" aria-hidden="true" />Weakens the evidence</h3>
            <ul className="list-disc space-y-1 pl-5">{f.caveats.map((x, i) => <li key={i}>{x}</li>)}</ul></div>)}
        <details className="rounded-md border p-3" data-testid="case-trace">
          <summary className="flex cursor-pointer items-center gap-1.5 font-semibold"><Route className="size-4" aria-hidden="true" />How the Case Investigator reached this ({f.trace.length} steps)</summary>
          <ol className="mt-2 space-y-2">
            {f.trace.map((t) => (
              <li key={t.step as number} className="flex gap-2"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-normal-fg" aria-hidden="true" />
                <div><div className="font-medium">{TOOL[t.tool as string] ?? String(t.tool)}</div><div className="text-muted-foreground">{String(t.why)}</div><div>{stepText(t as { tool: string; result: unknown })}</div></div></li>))}
          </ol>
          <p className="mt-2 text-xs text-muted-foreground">Rules that fired: {f.reasons.length ? f.reasons.join("; ") : "none (data check only)"}</p>
        </details>
        <p className="text-xs text-muted-foreground">{f.disclaimer}</p>
      </CardContent>
    </Card>
  )
}
