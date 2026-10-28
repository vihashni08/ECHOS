import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { API_URL, ApiError, call, client } from "./api"
import { getToken } from "./auth"

const poll = (ms: number) => ({ refetchInterval: ms, retry: 1 })

// ---- patients, replay, system
export const usePatients = () => useQuery({ queryKey: ["patients"], queryFn: () => call(client.GET("/api/patients", { params: { query: { limit: 200 } } })), ...poll(5000) })
export const useStatuses = (pid: string, days = 120) =>
  useQuery({ queryKey: ["statuses", pid, days], enabled: !!pid, queryFn: () => call(client.GET("/api/patients/{pid}/statuses", { params: { path: { pid }, query: { days } } })), ...poll(5000) })
export const useSimState = () => useQuery({ queryKey: ["sim"], queryFn: () => call(client.GET("/api/sim/state")), ...poll(3000) })
export const useConfig = () => useQuery({ queryKey: ["config"], queryFn: () => call(client.GET("/api/system/config")), staleTime: 60000, retry: 1 })
export const useMetrics = () => useQuery({ queryKey: ["metrics"], queryFn: () => call(client.GET("/api/system/metrics")), staleTime: 60000, retry: 1 })
export const useLimitations = () => useQuery({ queryKey: ["limits"], queryFn: () => call(client.GET("/api/system/limitations")), staleTime: 60000, retry: 1 })
export const useAgents = () => useQuery({ queryKey: ["agents"], queryFn: () => call(client.GET("/api/agents/health")), ...poll(2000) })
export const useComponents = () => useQuery({ queryKey: ["components"], queryFn: () => call(client.GET("/api/components")), ...poll(3000) })
export const useMessages = (limit = 60, pid?: string) =>
  useQuery({ queryKey: ["messages", limit, pid], queryFn: () => call(client.GET("/api/agents/messages", { params: { query: { limit, patient_id: pid } } })), ...poll(2000) })

// ---- v2: review list, monitor timeline, summary
export const useReview = (day?: string) => useQuery({ queryKey: ["review", day], queryFn: () => call(client.GET("/api/v2/review", { params: { query: { day } } })), ...poll(3000) })
export const useMonitor = (pid: string, limit = 60) =>
  useQuery({ queryKey: ["monitor", pid, limit], enabled: !!pid, queryFn: () => call(client.GET("/api/v2/patients/{pid}/monitor", { params: { path: { pid }, query: { limit } } })), ...poll(4000) })
export type Summary = { patient_days_scored: number; urgent_alerts: number; reviews: number; reviews_confirmed: number; reviews_no_event: number;
  messages: Record<string, number>; case_status: Record<string, number> }
export const useSummary = () => useQuery({ queryKey: ["summary"], queryFn: async () => (await call(client.GET("/api/v2/summary"))) as unknown as Summary, ...poll(3000) })

export function useOutcome() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { patient_id: string; day: string; vitals_event: boolean; agitation_windows?: string[] }) => call(client.POST("/api/v2/reviews/outcome", { body })),
    onSettled: () => { qc.invalidateQueries({ queryKey: ["review"] }); qc.invalidateQueries({ queryKey: ["messages"] }) },
  })
}
export function useSimControl() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { action: "play" | "pause" | "speed" | "reset"; speed?: number }) => call(client.POST("/api/sim/control", { body })),
    onSettled: () => qc.invalidateQueries(),
  })
}
export function useInjectReading() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { patient_id: string; sys?: number; dia?: number; hr?: number; temp?: number }) => call(client.POST("/api/sim/reading", { body })),
    onSettled: () => qc.invalidateQueries(),
  })
}
export function useKill() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => call(client.POST("/api/agents/{name}/kill", { params: { path: { name }, query: { wait_s: 120 } } })),
    onSettled: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  })
}

// ---- alerts, tasks, delivery log
export const useOutbox = (limit = 15) => useQuery({ queryKey: ["outbox", limit], queryFn: () => call(client.GET("/api/outbox", { params: { query: { limit } } })), refetchInterval: 5000, retry: 1 })
export const useAlertList = (limit = 50) => useQuery({ queryKey: ["alerts", "life", limit], queryFn: () => call(client.GET("/api/alerts", { params: { query: { limit } } })), ...poll(2000) })
export const useTasks = (status: "OPEN" | "DONE" | undefined = "OPEN") =>
  useQuery({ queryKey: ["tasks", status], queryFn: () => call(client.GET("/api/tasks", { params: { query: { status, limit: 100 } } })), ...poll(3000) })
export function useAck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; by?: string }) => call(client.POST("/api/alerts/{alert_id}/ack", { params: { path: { alert_id: v.id } }, body: { by: v.by ?? "caregiver" } })),
    onSettled: () => { qc.invalidateQueries({ queryKey: ["alerts"] }); qc.invalidateQueries({ queryKey: ["tasks"] }) },
  })
}
export function useResolve() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; note?: string }) => call(client.POST("/api/alerts/{alert_id}/resolve", { params: { path: { alert_id: v.id } }, body: { by: "caregiver", note: v.note } })),
    onSettled: () => { qc.invalidateQueries({ queryKey: ["alerts"] }); qc.invalidateQueries({ queryKey: ["tasks"] }) },
  })
}

// ---- authentication (optional on the server)
export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => call(client.GET("/api/auth/me")), retry: 1, staleTime: 5000 })

// ---- Evidence agent, decision trace, patient memory, demo data
export type Passage = { id: string; doc: string; source: string; url: string; page: number; text: string; score: number }
export type EvidenceCardT = { card_id: string; patient_id: string; day: string; kind: "urgent" | "review"; source: "gemini" | "template"; explanation: string; citations: string[];
  passages: Passage[]; validator: { ok?: boolean | null; problems?: string[] }; model?: string | null }
export const useEvidence = (day?: string, patientId?: string) =>
  useQuery({ queryKey: ["evidence", day, patientId], queryFn: async () => (await call(client.GET("/api/v2/evidence", { params: { query: { day, patient_id: patientId } } }))) as unknown as EvidenceCardT[], ...poll(3000) })
export type Trace = { patient_id: string; day: string; status?: string | null; monitor?: Record<string, any> | null; review_request?: Record<string, any> | null;
  review?: Record<string, any> | null; coordinator?: Record<string, any> | null; care_task?: Record<string, any> | null; alerts: { alert_id: string; event_time?: string }[];
  evidence: EvidenceCardT[]; messages: { id: number; sender: string; recipient: string; topic: string; status: string }[] }
async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(`${API_URL}${path}`, { headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {} })
  if (!r.ok) throw new ApiError(r.status, r.status === 404 ? "Data file missing: run scripts/evaluation/demo_data.py" : `HTTP ${r.status}`)
  return r.json()
}
export const useTrace = (pid?: string, day?: string) => useQuery({ queryKey: ["trace", pid, day], enabled: !!pid && !!day, queryFn: () => getJson<Trace>(`/api/v2/trace/${pid}/${day}`), ...poll(3000) })
export type Memory = { patient_id: string; known: boolean; vitals_history?: Record<string, number[]>; events_k?: number; days_n?: number; windows_seen?: number; episodes?: number;
  slot_history_windows?: Record<string, number>; days_scored?: number; pending_days?: string[]; numbers_stored?: number }
export const useMemoryCard = (pid?: string) => useQuery({ queryKey: ["memory", pid], enabled: !!pid, queryFn: () => getJson<Memory>(`/api/v2/patients/${pid}/memory`), ...poll(4000) })
export type AuditGrid = { cells: { sys: number; dia: number; n: number; labelled: number }[]; wall: { rule: string; days: number; share_labelled: number; below_days: number; below_share_labelled: number } }
export const useDemoAudit = () => useQuery({ queryKey: ["demo-audit"], queryFn: () => getJson<AuditGrid>("/api/v2/demo/audit"), staleTime: 600000, retry: 1 })
export type BudgetRow = { events_per_review: number; agitation_followed_up: number; vitals_anticipated: number; patients_never_reviewed: number; max_days_without_review: number }
export type Budget = { k: number[]; policies: Record<string, BudgetRow[]> }
export const useDemoBudget = () => useQuery({ queryKey: ["demo-budget"], queryFn: () => getJson<Budget>("/api/v2/demo/budget"), staleTime: 600000, retry: 1 })
export type GamePerson = { id: string; sys: number | null; dia: number | null; hr: number | null; sys_z: number | null; events_14d: number; activity: number | null; activity_z: number | null;
  tihm_flag: boolean; agitation_episodes: number; vitals_events_next3: number; found: number; lift: number }
export type GameDay = { day: string; k: number; patients: GamePerson[]; echos: string[]; threshold: string[]; echos_found: number; threshold_found: number;
  echos_split: { agitation: number; vitals: number }; threshold_split: { agitation: number; vitals: number }; random_found: number; best_possible: number }
export type Game = { k: number; found_definition: string; days: GameDay[] }
export const useDemoGame = () => useQuery({ queryKey: ["demo-game"], queryFn: () => getJson<Game>("/api/v2/demo/game"), staleTime: 600000, retry: 1 })
export const useRuntime = () => useQuery({ queryKey: ["runtime"], queryFn: () => getJson<Record<string, any>>("/api/v2/runtime"), staleTime: 600000, retry: 1 })

// ---- the daily review (one day, every patient, all four agents)
type Vit = { value?: number | null; usual?: number | null; z?: number | null; n_past?: number }
export type DayWindow = { w: string; slot: number; p: number; reasons: string[]; events?: number; usual?: number; silent?: boolean }
export type DayPatient = {
  patient_id: string; scored: boolean; status?: string | null; vitals: Record<string, Vit>; urgent: string[]; attention: string[]; agitation: DayWindow[]
  agitation_max?: number | null; quality?: { flags?: string[]; device_problem?: boolean }; injected?: boolean
  vitals_risk?: number | null; agitation_risk?: number | null; rate?: number | null; rate_ci95?: number[] | null; reasons: string[]
  lift_vitals?: number | null; lift_agitation?: number | null; priority?: number | null
  rank?: number | null; pick_reason?: string | null; outcome?: string | null
  alerts: { alert_id: string; reasons?: string[] | null; severity?: string | null }[]; evidence: EvidenceCardT[]
}
export type DayView = { day: string | null; days: string[]; allocated: boolean; k: number; base_rates: { v_bar?: number | null; a_bar?: number | null }; patients: DayPatient[] }
export const useDay = (day?: string) =>
  useQuery({ queryKey: ["day", day], queryFn: async () => (await call(client.GET("/api/v2/day", { params: { query: { day } } }))) as unknown as DayView, ...poll(3000) })
