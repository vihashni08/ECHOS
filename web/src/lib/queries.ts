import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { call, client } from "./api"

const poll = (ms: number) => ({ refetchInterval: ms, retry: 1 })

export const usePatients = () => useQuery({ queryKey: ["patients"], queryFn: () => call(client.GET("/api/patients", { params: { query: { limit: 100 } } })), ...poll(10000) })
export const useSimState = () => useQuery({ queryKey: ["sim"], queryFn: () => call(client.GET("/api/sim/state")), ...poll(3000) })
export const useConfig = () => useQuery({ queryKey: ["config"], queryFn: () => call(client.GET("/api/system/config")), staleTime: 60000, retry: 1 })
export const useAgents = () => useQuery({ queryKey: ["agents"], queryFn: () => call(client.GET("/api/agents/health")), ...poll(2000) })
export const useAlerts = (limit = 30) => useQuery({ queryKey: ["alerts", limit], queryFn: () => call(client.GET("/api/alerts", { params: { query: { limit } } })), retry: 1 })
export const useFalls = () => useQuery({ queryKey: ["falls"], queryFn: () => call(client.GET("/api/falls", { params: { query: { limit: 200 } } })), ...poll(5000) })
export const useFall = (id: number | undefined, poll_ = false) =>
  useQuery({ queryKey: ["fall", id], enabled: id !== undefined, retry: false, refetchInterval: poll_ ? 1500 : false,
    queryFn: () => call(client.GET("/api/falls/{fid}", { params: { path: { fid: id! } } })) })
export const useSummary = (pid: string) => useQuery({ queryKey: ["summary", pid], queryFn: () => call(client.GET("/api/patients/{pid}/summary", { params: { path: { pid } } })), retry: 1 })
export const useStatuses = (pid: string, days = 120) =>
  useQuery({ queryKey: ["statuses", pid, days], queryFn: () => call(client.GET("/api/patients/{pid}/statuses", { params: { path: { pid }, query: { days } } })), retry: 1 })
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
    mutationFn: (body: { patient_id: string }) => call(client.POST("/api/sim/fall", { body })),
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
