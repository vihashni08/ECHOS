import { useState } from "react"
import { CheckCircle2, AlertTriangle, Skull } from "lucide-react"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useAgents, useConfig, useKill } from "@/lib/queries"

const NAME: Record<string, string> = { quality: "Data quality", health_monitoring: "Health monitoring", fall: "Fall" }

export default function AgentStrip() {
  const agents = useAgents()
  const cfg = useConfig()
  const kill = useKill()
  const [target, setTarget] = useState<string | null>(null)
  const [result, setResult] = useState<string>("")
  return (
    <Card>
      <CardHeader><CardTitle>Agents</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3">
        {!agents.data ? <Skeleton className="h-16 w-full" /> : (
          <ul className="grid gap-2 sm:grid-cols-3" data-testid="agent-strip">
            {agents.data.map((a) => (
              <li key={a.agent} className="flex flex-col gap-1.5 rounded-lg border p-3 text-sm">
                <div className="font-medium">{NAME[a.agent] ?? a.agent}</div>
                <div className={`inline-flex items-center gap-1.5 font-semibold ${a.alive ? "text-normal-fg" : "text-urgent-fg"}`}>
                  {a.alive ? <CheckCircle2 className="size-4" aria-hidden="true" /> : <AlertTriangle className="size-4" aria-hidden="true" />}
                  {a.alive ? "alive" : "stale"}
                  <span className="num font-normal text-muted-foreground">{a.heartbeat_age_s == null ? "no heartbeat" : `${a.heartbeat_age_s.toFixed(0)} s ago`}</span>
                </div>
                {cfg.data?.allow_kill && (
                  <Button size="sm" variant="outline" className="mt-1 self-start" onClick={() => setTarget(a.agent)} disabled={kill.isPending} aria-label={`Kill ${NAME[a.agent] ?? a.agent} agent`}>
                    <Skull className="size-4" aria-hidden="true" /> Kill agent
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {kill.isPending && <p className="text-sm text-muted-foreground" role="status">Waiting for the supervisor to bring the agent back…</p>}
        {result && !kill.isPending && <p className="text-sm font-medium" role="status" data-testid="kill-result">{result}</p>}
      </CardContent>
      <AlertDialog open={target !== null} onOpenChange={(o) => !o && setTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Kill the {NAME[target ?? ""] ?? target} agent?</AlertDialogTitle>
            <AlertDialogDescription>Fault demo: the worker process is killed and the supervisor restarts it from its checkpoint (about 12 s). Events keep queueing; no alert is lost.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => {
              const t = target!
              setResult("")
              kill.mutate(t, { onSuccess: (r) => setResult(r.recovered ? `${NAME[t] ?? t} agent is alive again after ${r.recovery_s} s.` : `${NAME[t] ?? t} agent did not recover in time.`), onError: (e) => setResult((e as Error).message) })
            }}>Kill agent</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}
