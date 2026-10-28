import { ChevronLeft, ChevronRight, Presentation, X } from "lucide-react"
import { useEffect, useState } from "react"
import { useLocation, useNavigate } from "react-router-dom"
import { Button } from "@/components/ui/button"

/** Presenter mode: a strip with the step number and the one-line script, so the demo never depends on memory. Steps move between the screens. */
export const STEPS: { path: string; title: string; line: string }[] = [
  { path: "/how", title: "The question", line: "A small team can review only a few people a day. ECHOS answers: who, today, and why? It is decision support, not a diagnosis." },
  { path: "/test?tab=audit", title: "Audit first", line: "Labels are confirmed threshold alerts. Almost every day above 160/100 is labelled, so 'detecting' them would re-learn a threshold. That is why thresholds stay rules." },
  { path: "/test?tab=game", title: "You vs ECHOS", line: "Pick four people to review. Then see what TIHM confirmed. A BP-limit rule wins on BP events, ECHOS covers agitation too: it is a balanced policy." },
  { path: "/", title: "Daily review", line: "One day, every patient: the readings against each person's usual, the top 4 picked and why, with the guideline passage that supports each reason." },
  { path: "/trace", title: "Decision trace", line: "One person, one day, every step: inputs, personal baseline, risk, lift, allocation, messages, evidence. No hidden model per person: a few numbers of memory." },
  { path: "/test?tab=budget", title: "The budget is a decision", line: "Move K and the fairness floor and watch events per review and never-reviewed patients change." },
  { path: "/agents", title: "Under the hood", line: "Four agents, one bus. Inject a red-flag reading, then kill an agent: the crash run matches the uninterrupted run." },
  { path: "/agents#risks", title: "Risks and limits", line: "Say the limits before they ask: small cohort, confirmed alerts as labels, replay not live, no clinical validation." },
]
const KEY = "echos-present"
const read = () => { try { return localStorage.getItem(KEY) } catch { return null } }

export function PresenterToggle() {
  const [on, setOn] = useState(() => read() !== null)
  const nav = useNavigate()
  useEffect(() => { const f = () => setOn(read() !== null); window.addEventListener("storage", f); window.addEventListener("echos-present", f); return () => { window.removeEventListener("storage", f); window.removeEventListener("echos-present", f) } }, [])
  return (
    <Button variant={on ? "default" : "outline"} size="sm" onClick={() => { try { on ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, "0") } catch { /* private mode */ } window.dispatchEvent(new Event("echos-present")); if (!on) nav(STEPS[0].path) }}
      aria-pressed={on} data-testid="presenter-toggle"><Presentation className="size-4" aria-hidden="true" />Presenter</Button>
  )
}

export function PresenterBar() {
  const [step, setStep] = useState<number | null>(() => { const v = read(); return v === null ? null : Number(v) || 0 })
  const nav = useNavigate()
  const loc = useLocation()
  useEffect(() => { const f = () => { const v = read(); setStep(v === null ? null : Number(v) || 0) }; window.addEventListener("echos-present", f); return () => window.removeEventListener("echos-present", f) }, [])
  if (step === null) return null
  const go = (i: number) => { const n = Math.min(Math.max(i, 0), STEPS.length - 1); try { localStorage.setItem(KEY, String(n)) } catch { /* private mode */ } setStep(n); nav(STEPS[n].path) }
  const s = STEPS[step]
  const off = !(loc.pathname + loc.search).startsWith(s.path.split("#")[0].split("?")[0]) // moved away from the step's screen: still show the line
  return (
    <div className="fixed inset-x-0 bottom-0 z-40 flex flex-wrap items-center gap-3 border-t bg-card px-4 py-2.5 shadow-lg" role="region" aria-label="Presenter" data-testid="presenter-bar">
      <span className="num text-sm font-semibold">Step {step + 1} of {STEPS.length}</span>
      <div className="min-w-0 flex-1 text-sm"><span className="font-semibold">{s.title}.</span> {s.line}{off && <span className="ml-1 text-muted-foreground">(you are on another page)</span>}</div>
      <div className="flex items-center gap-1.5">
        <Button size="sm" variant="outline" onClick={() => go(step - 1)} disabled={step === 0} aria-label="Previous step"><ChevronLeft className="size-4" aria-hidden="true" /></Button>
        <Button size="sm" onClick={() => go(step + 1)} disabled={step === STEPS.length - 1} aria-label="Next step"><ChevronRight className="size-4" aria-hidden="true" />Next</Button>
        <Button size="sm" variant="outline" onClick={() => { try { localStorage.removeItem(KEY) } catch { /* private mode */ } window.dispatchEvent(new Event("echos-present")) }} aria-label="Close presenter"><X className="size-4" aria-hidden="true" /></Button>
      </div>
    </div>
  )
}
