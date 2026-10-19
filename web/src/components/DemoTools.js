import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import AgentStrip from "./AgentStrip";
import EmulatorControls from "./EmulatorControls";
import SimControls from "./SimControls";
import { useMode } from "@/lib/live";
/** Everything that drives the data source lives here, never on the home page. */
export function DemoToolsButton() {
    const { mode } = useMode();
    const [open, setOpen] = useState(false);
    if (mode === "live")
        return null;
    return (_jsxs(_Fragment, { children: [_jsxs(Button, { variant: "outline", onClick: () => setOpen(true), "data-testid": "demo-tools-button", children: [_jsx(Wrench, { className: "size-4", "aria-hidden": "true" }), "Demo tools"] }), _jsx(Sheet, { open: open, onOpenChange: setOpen, children: _jsxs(SheetContent, { className: "w-full overflow-y-auto sm:max-w-lg", children: [_jsxs(SheetHeader, { children: [_jsx(SheetTitle, { children: "Demo tools" }), _jsx(SheetDescription, { children: mode === "emulator" ? "Control the labelled device emulator." : "Control the day-by-day replay of the TIHM study." })] }), _jsx("div", { className: "flex flex-col gap-4 px-4 pb-6", children: mode === "emulator" ? _jsx(EmulatorControls, {}) : _jsxs(_Fragment, { children: [_jsx(SimControls, {}), _jsx(AgentStrip, {})] }) })] }) })] }));
}
