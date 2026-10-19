import { jsx as _jsx } from "react/jsx-runtime";
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { API_URL } from "./api";
import { useToken } from "./auth";
const Ctx = createContext({ conn: "connecting", feed: [] });
/** One EventSource for the whole app (auto-reconnect: the browser resends Last-Event-ID). Events invalidate the cached queries. */
export function EventsProvider({ children }) {
    const qc = useQueryClient();
    const [conn, setConn] = useState("connecting");
    const [feed, setFeed] = useState([]);
    const seen = useRef(new Set());
    const token = useToken();
    useEffect(() => {
        const es = new EventSource(`${API_URL}/api/events${token ? `?token=${encodeURIComponent(token)}` : ""}`);
        es.onopen = () => setConn("open");
        es.onerror = () => setConn(es.readyState === EventSource.CLOSED ? "connecting" : "reconnecting");
        const refresh = (keys) => keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
        es.addEventListener("alert", (ev) => {
            const d = JSON.parse(ev.data);
            if (!seen.current.has(d.alert_id)) {
                seen.current.add(d.alert_id);
                setFeed((f) => [{ id: ev.lastEventId, kind: "alert", alert_id: d.alert_id, patient_id: d.patient_id, type: d.type, event_time: d.event_time ?? null, seen: Date.now() }, ...f].slice(0, 50));
            }
            refresh(["alerts", "falls", "patients", "fall", "live", "case"]);
        });
        es.addEventListener("day_status", () => refresh(["patients", "summary", "statuses", "falls", "sim", "live", "case"]));
        es.addEventListener("outbox_status", () => refresh(["falls", "fall"]));
        es.addEventListener("report_ready", () => refresh(["patients"]));
        es.addEventListener("agent_health", () => refresh(["agents", "components"]));
        es.addEventListener("alert_state", () => refresh(["alerts", "tasks", "components"]));
        es.addEventListener("agent_message", () => refresh(["messages"]));
        es.addEventListener("care_task", () => refresh(["tasks"]));
        return () => es.close();
    }, [qc, token]);
    return _jsx(Ctx.Provider, { value: { conn, feed }, children: children });
}
export const useEvents = () => useContext(Ctx);
