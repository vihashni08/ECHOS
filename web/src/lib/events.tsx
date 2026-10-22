import { useQueryClient } from "@tanstack/react-query"
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react"
import { API_URL, type AlertRow } from "./api"
import { useToken } from "./auth"

export type Conn = "connecting" | "open" | "reconnecting"
export type FeedItem = { id: string; kind: "alert"; alert_id: string; patient_id: string; type: string; event_time: string | null; seen: number }
const Ctx = createContext<{ conn: Conn; feed: FeedItem[] }>({ conn: "connecting", feed: [] })

/** One EventSource for the whole app (auto-reconnect: the browser resends Last-Event-ID). Events invalidate the cached queries. */
export function EventsProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [conn, setConn] = useState<Conn>("connecting")
  const [feed, setFeed] = useState<FeedItem[]>([])
  const seen = useRef(new Set<string>())
  const token = useToken()

  useEffect(() => {
    const es = new EventSource(`${API_URL}/api/events${token ? `?token=${encodeURIComponent(token)}` : ""}`)
    es.onopen = () => setConn("open")
    es.onerror = () => setConn(es.readyState === EventSource.CLOSED ? "connecting" : "reconnecting")
    // a fast replay emits many events per second: collect the query keys and refetch each at most once a second
    const pending = new Set<string>()
    let timer: ReturnType<typeof setTimeout> | null = null
    const refresh = (keys: string[]) => {
      keys.forEach((k) => pending.add(k))
      if (!timer) timer = setTimeout(() => { pending.forEach((k) => qc.invalidateQueries({ queryKey: [k] })); pending.clear(); timer = null }, 1000)
    }
    es.addEventListener("alert", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data) as AlertRow
      if (!seen.current.has(d.alert_id)) {
        seen.current.add(d.alert_id)
        setFeed((f) => [{ id: (ev as MessageEvent).lastEventId, kind: "alert" as const, alert_id: d.alert_id, patient_id: d.patient_id, type: d.type, event_time: d.event_time ?? null, seen: Date.now() }, ...f].slice(0, 50))
      }
      refresh(["alerts", "patients", "summary", "monitor"])
    })
    es.addEventListener("day_status", () => refresh(["patients", "summary", "statuses", "sim", "monitor"]))
    es.addEventListener("outbox_status", () => refresh(["outbox", "alerts"]))
    es.addEventListener("review_list", () => refresh(["review", "summary", "statuses"]))
    es.addEventListener("agent_health", () => refresh(["agents", "components"]))
    es.addEventListener("alert_state", () => refresh(["alerts", "tasks", "components"]))
    es.addEventListener("agent_message", () => refresh(["messages", "monitor"]))
    es.addEventListener("care_task", () => refresh(["tasks"]))
    return () => { es.close(); if (timer) clearTimeout(timer) }
  }, [qc, token])
  return <Ctx.Provider value={{ conn, feed }}>{children}</Ctx.Provider>
}
export const useEvents = () => useContext(Ctx)
