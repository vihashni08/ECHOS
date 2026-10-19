import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Inbox } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
export function Empty({ title, children }) {
    return (_jsxs("div", { className: "flex flex-col items-center gap-1 rounded-lg border border-dashed px-4 py-8 text-center", children: [_jsx(Inbox, { className: "size-6 text-muted-foreground", "aria-hidden": "true" }), _jsx("p", { className: "font-medium", children: title }), children && _jsx("p", { className: "max-w-md text-sm text-muted-foreground", children: children })] }));
}
export const CardSkeleton = ({ h = "h-32" }) => _jsx(Skeleton, { className: `${h} w-full`, role: "status", "aria-label": "Loading" });
export function ErrorNote({ error }) {
    return _jsx("p", { role: "alert", className: "rounded-md border border-urgent-line bg-urgent-bg px-3 py-2 text-sm text-urgent-fg", children: error instanceof Error ? error.message : "Something went wrong" });
}
