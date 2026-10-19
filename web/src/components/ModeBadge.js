import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Info } from "lucide-react";
import { MODE, useMode } from "@/lib/live";
import { cn } from "@/lib/utils";
/** Which kind of data the whole page shows: always visible, never colour alone. */
export function ModeBadge({ className }) {
    const { mode } = useMode();
    const m = MODE[mode];
    return _jsx("span", { title: m.what, "data-testid": "mode-badge", className: cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-bold tracking-wide", m.cls, className), children: m.label });
}
export function ModeBanner() {
    const { mode } = useMode();
    if (mode === "live")
        return null;
    const m = MODE[mode];
    return (_jsxs("div", { role: "note", "data-testid": "mode-banner", className: cn("flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2 text-sm", m.cls), children: [_jsx(Info, { className: "size-4 shrink-0", "aria-hidden": "true" }), _jsxs("span", { children: [_jsxs("strong", { children: [m.label, ":"] }), " ", m.what] })] }));
}
