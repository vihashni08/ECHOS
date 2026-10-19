import { useSyncExternalStore } from "react";
// The bearer token lives in sessionStorage only (gone when the tab closes). It is never put in a URL except for the event stream, which cannot set headers.
const KEY = "echos-token";
const subs = new Set();
export const getToken = () => { try {
    return sessionStorage.getItem(KEY);
}
catch {
    return null;
} };
export function setToken(t) {
    try {
        t ? sessionStorage.setItem(KEY, t) : sessionStorage.removeItem(KEY);
    }
    catch { /* private mode: the token then lives only until reload */ }
    subs.forEach((f) => f());
}
export const useToken = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, getToken);
export const LEVEL = { caregiver: 1, clinician: 2, operator: 3 };
export const atLeast = (role, need) => !!role && (LEVEL[role] ?? 0) >= LEVEL[need];
