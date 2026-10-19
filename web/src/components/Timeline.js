import { jsx as _jsx } from "react/jsx-runtime";
import { Link } from "react-router-dom";
import { useStatuses } from "@/lib/queries";
import { STATUS } from "@/lib/status";
import { cn } from "@/lib/utils";
/** Last N days as coloured blocks WITH a letter: N normal, M monitor, U urgent (never colour alone). */
export default function Timeline({ pid, days = 14, link = false }) {
    const st = useStatuses(pid, days);
    const rows = (st.data ?? []).slice(-days);
    if (!rows.length)
        return _jsx("span", { className: "text-sm text-muted-foreground", children: "no days yet" });
    const L = { NORMAL: "N", MONITOR: "M", URGENT: "U" };
    const body = (_jsx("ol", { className: "flex gap-0.5", "aria-label": `Last ${rows.length} days`, "data-testid": "timeline", children: rows.map((d) => {
            const s = STATUS[d.status] ?? STATUS.DATA_GAP;
            return _jsx("li", { title: `${d.day}: ${s.label}`, className: cn("num flex h-6 w-6 items-center justify-center rounded border text-[11px] font-bold", s.cls), children: L[d.status] ?? "?" }, d.day);
        }) }));
    return link ? _jsx(Link, { to: `/patients/${pid}`, "aria-label": `Open patient ${pid}`, children: body }) : body;
}
