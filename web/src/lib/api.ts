import createClient from "openapi-fetch"
import type { components, paths } from "./api-schema"
import { getToken, setToken } from "./auth"

// Typed client generated from the FastAPI OpenAPI schema (npm run gen:api). No runtime request goes anywhere but this base URL.
export const API_URL: string = import.meta.env.VITE_API_URL ?? __API_DEFAULT__
export const client = createClient<paths>({ baseUrl: API_URL })
client.use({
  onRequest({ request }) { const t = getToken(); if (t) request.headers.set("Authorization", `Bearer ${t}`); return request },
  onResponse({ response }) { if (response.status === 401 && getToken()) setToken(null); return response },     // a token the server no longer accepts: back to the login page
})

export type Schemas = components["schemas"]
export type Patient = Schemas["PatientOut"]
export type FallItem = Schemas["FallItem"]
export type FallDetail = Schemas["FallDetail"]
export type Outbox = Schemas["OutboxOut"]
export type AlertRow = Schemas["AlertOut"]
export type Vitals = Schemas["VitalsOut"]
export type Routine = Schemas["RoutineOut"]
export type SimState = Schemas["SimStateOut"]
export type AgentHealth = Schemas["AgentHealth"]
export type LiveStatus = Schemas["LiveStatusOut"]
export type Component = Schemas["ComponentOut"]
export type CaseFile = Schemas["CaseFileOut"]
export type CareTask = Schemas["CareTaskOut"]
export type AgentMessage = Schemas["AgentMessageOut"]

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** Unwrap an openapi-fetch result: data or a thrown ApiError (status 0 = API unreachable). */
export function unwrap<T>(r: { data?: T; error?: unknown; response: Response }): T {
  if (r.data !== undefined) return r.data
  const e = r.error as { error?: { message?: string } } | undefined
  throw new ApiError(r.response.status, e?.error?.message ?? `HTTP ${r.response.status}`)
}

export async function call<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  try {
    return unwrap(await p)
  } catch (e) {
    if (e instanceof ApiError) throw e
    throw new ApiError(0, "API not reachable")
  }
}

export type AgentSummary = Schemas["AgentSummaryOut"]
export type ActivityEvent = Schemas["ActivityEventOut"]
