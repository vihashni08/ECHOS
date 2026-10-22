import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { call, client } from "./api"

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
