import createClient from "openapi-fetch";
import { getToken, setToken } from "./auth";
// Typed client generated from the FastAPI OpenAPI schema (npm run gen:api). No runtime request goes anywhere but this base URL.
export const API_URL = import.meta.env.VITE_API_URL ?? __API_DEFAULT__;
export const client = createClient({ baseUrl: API_URL });
client.use({
    onRequest({ request }) { const t = getToken(); if (t)
        request.headers.set("Authorization", `Bearer ${t}`); return request; },
    onResponse({ response }) { if (response.status === 401 && getToken())
        setToken(null); return response; }, // a token the server no longer accepts: back to the login page
});
export class ApiError extends Error {
    status;
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}
/** Unwrap an openapi-fetch result: data or a thrown ApiError (status 0 = API unreachable). */
export function unwrap(r) {
    if (r.data !== undefined)
        return r.data;
    const e = r.error;
    throw new ApiError(r.response.status, e?.error?.message ?? `HTTP ${r.response.status}`);
}
export async function call(p) {
    try {
        return unwrap(await p);
    }
    catch (e) {
        if (e instanceof ApiError)
            throw e;
        throw new ApiError(0, "API not reachable");
    }
}
