import { Users } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useTeamPlan } from "@/lib/queries"
import { fmtDay } from "@/lib/format"
import { cn } from "@/lib/utils"

const NAME: Record<string, string> = { triage: "Triage", investigator: "Case Investigator", coordinator: "Care Coordinator", report: "Report agent" }
const ROLE: Record<string, string> = { owner: "leads", watch: "short watch file (near miss)", passive: "listens only", template: "fixed template", llm_eligible: "wording may use the LLM" }

/** "Who worked on this day": the Team Planner's choice of agents for the day, with a reason for each (rules, not a model). */
export default function TeamPlanPanel({ pid, day }: { pid: string; day?: string }) {
  const p = useTeamPlan(pid, day)
  if (!p.data) return null
  return (
    <Card data-testid="team-plan">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2"><Users className="size-5" aria-hidden="true" />Who worked on this day · <span className="num">{fmtDay(p.data.day)}</span></CardTitle>
        <span className="rounded-full border px-2.5 py-0.5 text-xs font-semibold" data-testid="team-size">{p.data.team_size} of {p.data.agents.length} agents</span>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <ul className="space-y-2">
          {p.data.agents.map((a) => (
            <li key={a.id} className={cn("flex gap-2", !a.active && "text-muted-foreground")} data-testid={`plan-${a.id}`}>
              <span className={cn("mt-0.5 inline-block w-14 shrink-0 text-xs font-semibold", a.active ? "text-normal-fg" : "")}>{a.active ? "Used" : "Not needed"}</span>
              <div><div className="font-medium">{NAME[a.id] ?? a.id}{a.active && ROLE[a.role] ? <span className="font-normal text-muted-foreground"> · {ROLE[a.role]}</span> : null}{a.need != null && a.threshold != null ? <span className="num ml-2 text-xs font-normal text-muted-foreground" title="nearest-threshold ratio r (highest detector score over its own alert threshold) against the near-miss cutoff">r {a.need.toFixed(2)} {a.need > a.threshold ? ">" : "≤"} {a.threshold.toFixed(2)}</span> : null}</div><div>{a.reason}</div></div>
            </li>))}
        </ul>
        {p.data.closest && <p className="text-xs text-muted-foreground" data-testid="team-closest">Closest to an alert: {p.data.closest}.</p>}
        <p className="text-xs text-muted-foreground">{p.data.max_llm_requests === 0 ? "The report for this day uses a template: no LLM request." : `The report may use at most ${p.data.max_llm_requests} LLM requests.`} Chosen from the day's status and how close a detector came to its own alert threshold; it never changes a status or delays an alert.</p>
      </CardContent>
    </Card>
  )
}
