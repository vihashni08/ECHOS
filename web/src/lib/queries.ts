import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ApiError, call, client } from "./api"

const poll = (ms: number) => ({ refetchInterval: ms, retry: 1 })

export const usePatients = () => useQuery({ queryKey: ["patients"], queryFn: () => call(client.GET("/api/patients", { params: { query: { limit: 100 } } })), ...poll(10000) })
export const useSimState = () => useQuery({ queryKey: ["sim"], queryFn: () => call(client.GET("/api/sim/state")), ...poll(3000) })
export const useConfig = () => useQuery({ queryKey: ["config"], queryFn: () => call(client.GET("/api/system/config")), staleTime: 60000, retry: 1 })
export const useAgents = () => useQuery({ queryKey: ["agents"], queryFn: () => call(client.GET("/api/agents/health")), ...poll(2000) })
export const useFalls = () => useQuery({ queryKey: ["falls"], queryFn: () => call(client.GET("/api/falls", { params: { query: { limit: 200 } } })), ...poll(5000) })
export const useFall = (id: number | undefined, poll_ = false) =>
  useQuery({ queryKey: ["fall", id], enabled: id !== undefined, retry: false, refetchInterval: poll_ ? 1500 : false,
    queryFn: () => call(client.GET("/api/falls/{fid}", { params: { path: { fid: id! } } })) })
export const useSummary = (pid: string, day?: string) => useQuery({ queryKey: ["summary", pid, day], enabled: !!pid, queryFn: () => call(client.GET("/api/patients/{pid}/summary", { params: { path: { pid }, query: { day } } })), retry: 1 })
export const useStatuses = (pid: string, days = 120) =>
  useQuery({ queryKey: ["statuses", pid, days], enabled: !!pid, queryFn: () => call(client.GET("/api/patients/{pid}/statuses", { params: { path: { pid }, query: { days } } })), retry: 1 })
export const useVitals = (pid: string, from?: string, to?: string) =>
  useQuery({ queryKey: ["vitals", pid, from, to], enabled: !!from && !!to, retry: 1,
    queryFn: () => call(client.GET("/api/patients/{pid}/vitals", { params: { path: { pid }, query: { from, to } } })) })
export const useRoutine = (pid: string, from?: string, to?: string) =>
  useQuery({ queryKey: ["routine", pid, from, to], enabled: !!from && !!to, retry: 1,
    queryFn: () => call(client.GET("/api/patients/{pid}/routine", { params: { path: { pid }, query: { from, to } } })) })

export function useSimControl() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { action: "play" | "pause" | "speed" | "reset"; speed?: number }) => call(client.POST("/api/sim/control", { body })),
    onSettled: () => qc.invalidateQueries(),
  })
}
export function useSimFall() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { patient_id: string; activity?: string; subject?: string }) => call(client.POST("/api/sim/fall", { body })),
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

// ---- reports, sources, system
const nullOn404 = async <T,>(p: Promise<T>): Promise<T | null> => { try { return await p } catch (e) { if (e instanceof ApiError && e.status === 404) return null; throw e } }
export const useDaily = (pid: string, day?: string) =>
  useQuery({ queryKey: ["daily", pid, day], enabled: !!pid && !!day, retry: false,
    queryFn: () => nullOn404(call(client.GET("/api/patients/{pid}/reports/daily", { params: { path: { pid }, query: { day } } }))) })
export const useWeekly = (pid: string, week?: string, enabled = true) =>
  useQuery({ queryKey: ["weekly", pid, week], enabled: !!pid && !!week && enabled, retry: false,
    queryFn: () => nullOn404(call(client.GET("/api/patients/{pid}/reports/weekly", { params: { path: { pid }, query: { week } } }))) })
export const useWeeklyAvailable = (pid: string) =>
  useQuery({ queryKey: ["weekly-available", pid], enabled: !!pid, retry: false, queryFn: () => call(client.GET("/api/patients/{pid}/reports/weekly-available", { params: { path: { pid } } })) })
export function useBuildWeekly() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { pid: string; week: string }) => call(client.POST("/api/patients/{pid}/reports/weekly/build", { params: { path: { pid: v.pid }, query: { week: v.week } } })),
    onSuccess: (_d, v) => { qc.invalidateQueries({ queryKey: ["weekly", v.pid, v.week] }); qc.invalidateQueries({ queryKey: ["weekly-available", v.pid] }) },
  })
}
export function useSendWeekly() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { pid: string; week: string; force: boolean }) =>
      call(client.POST("/api/patients/{pid}/reports/weekly/send", { params: { path: { pid: v.pid }, query: { week: v.week } }, body: { force: v.force } })),
    onSettled: () => { qc.invalidateQueries({ queryKey: ["outbox"] }); qc.invalidateQueries({ queryKey: ["weekly"] }) },
  })
}
export const useOutbox = (limit = 15) => useQuery({ queryKey: ["outbox", limit], queryFn: () => call(client.GET("/api/outbox", { params: { query: { limit } } })), refetchInterval: 5000, retry: 1 })
export const useSource = (id?: string) =>
  useQuery({ queryKey: ["source", id], enabled: !!id, retry: false, queryFn: () => call(client.GET("/api/sources/{sid}", { params: { path: { sid: id! } } })) })
export const useQuality = () => useQuery({ queryKey: ["quality"], queryFn: () => call(client.GET("/api/system/quality")), retry: 1 })
export const useMetrics = () => useQuery({ queryKey: ["metrics"], queryFn: () => call(client.GET("/api/system/metrics")), staleTime: 60000, retry: 1 })
export const useBudget = () => useQuery({ queryKey: ["budget"], queryFn: () => call(client.GET("/api/system/llm-budget")), refetchInterval: 10000, retry: 1 })
export const useLimitations = () => useQuery({ queryKey: ["limits"], queryFn: () => call(client.GET("/api/system/limitations")), staleTime: 60000, retry: 1 })

// ---- live mode, multi-agent layer, alert lifecycle
export const useLive = () => useQuery({ queryKey: ["live"], queryFn: () => call(client.GET("/api/live/status")), ...poll(2000) })
export const useComponents = () => useQuery({ queryKey: ["components"], queryFn: () => call(client.GET("/api/components")), ...poll(3000) })
export const useMessages = (limit = 40, pid?: string) =>
  useQuery({ queryKey: ["messages", limit, pid], queryFn: () => call(client.GET("/api/agents/messages", { params: { query: { limit, patient_id: pid } } })), ...poll(3000) })
export const useAlertList = (limit = 50) => useQuery({ queryKey: ["alerts", "life", limit], queryFn: () => call(client.GET("/api/alerts", { params: { query: { limit } } })), ...poll(2000) })
export const useTasks = (status: "OPEN" | "DONE" | undefined = "OPEN") =>
  useQuery({ queryKey: ["tasks", status], queryFn: () => call(client.GET("/api/tasks", { params: { query: { status, limit: 100 } } })), ...poll(3000) })
export const useCase = (pid: string, day?: string) =>
  useQuery({ queryKey: ["case", pid, day], enabled: !!pid, retry: false, refetchInterval: 4000,
    queryFn: async () => (await call(client.GET("/api/patients/{pid}/case", { params: { path: { pid }, query: { day } } }))) ?? null })
export const useTeamPlan = (pid: string, day?: string) =>
  useQuery({ queryKey: ["team-plan", pid, day], enabled: !!pid, retry: false, refetchInterval: 4000,
    queryFn: async () => (await call(client.GET("/api/patients/{pid}/team-plan", { params: { path: { pid }, query: { day } } }))) ?? null })
export function useAck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; by?: string }) => call(client.POST("/api/alerts/{alert_id}/ack", { params: { path: { alert_id: v.id } }, body: { by: v.by ?? "caregiver" } })),
    onSettled: () => { qc.invalidateQueries({ queryKey: ["alerts"] }); qc.invalidateQueries({ queryKey: ["tasks"] }); qc.invalidateQueries({ queryKey: ["components"] }) },
  })
}
export function useResolve() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; note?: string }) => call(client.POST("/api/alerts/{alert_id}/resolve", { params: { path: { alert_id: v.id } }, body: { by: "caregiver", note: v.note } })),
    onSettled: () => { qc.invalidateQueries({ queryKey: ["alerts"] }); qc.invalidateQueries({ queryKey: ["tasks"] }) },
  })
}
export function useTaskDone() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => call(client.POST("/api/tasks/{task_id}/done", { params: { path: { task_id: id } } })),
    onSettled: () => qc.invalidateQueries({ queryKey: ["tasks"] }),
  })
}
export function useEmulator() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { action: "load" | "play" | "pause" | "speed" | "fall"; speed?: number; patient_id?: string; subject?: string; activity?: string }) => call(client.POST("/api/live/emulator", { body })),
    onSettled: () => qc.invalidateQueries(),
  })
}

// ---- authentication (optional on the server)
export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => call(client.GET("/api/auth/me")), retry: 1, staleTime: 5000 })

export const useAgentSummary = () => useQuery({ queryKey: ["agents", "summary"], queryFn: () => call(client.GET("/api/agents/summary")), ...poll(2000) })
export const useActivity = (o: { agent?: string; pid?: string; day?: string; oldestFirst?: boolean; limit?: number }) =>
  useQuery({ queryKey: ["agents", "activity", o], queryFn: () => call(client.GET("/api/agents/activity", { params: { query: { limit: o.limit ?? 80, agent: o.agent, patient_id: o.pid, day: o.day, oldest_first: o.oldestFirst } } })), ...poll(2000) })
