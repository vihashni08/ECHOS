import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { HeartPulse, LogIn } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { client, call } from "@/lib/api";
import { setToken } from "@/lib/auth";
export default function Login() {
    const qc = useQueryClient();
    const [token, setT] = useState("");
    const [err, setErr] = useState("");
    const [busy, setBusy] = useState(false);
    const submit = async (e) => {
        e.preventDefault();
        setErr("");
        setBusy(true);
        try {
            await call(client.POST("/api/auth/login", { body: { token } }));
            setToken(token);
            setT("");
            await qc.invalidateQueries();
        }
        catch (x) {
            setErr(x.message);
        }
        finally {
            setBusy(false);
        }
    };
    return (_jsxs("main", { id: "main", className: "mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 p-4", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(HeartPulse, { className: "size-7 text-primary", "aria-hidden": "true" }), _jsxs("div", { children: [_jsx("h1", { className: "text-xl font-semibold", children: "ECHOS" }), _jsx("p", { className: "text-sm text-muted-foreground", children: "Decision support, not a diagnosis" })] })] }), _jsxs(Card, { children: [_jsx(CardHeader, { children: _jsx(CardTitle, { children: "Log in" }) }), _jsx(CardContent, { children: _jsxs("form", { onSubmit: submit, className: "flex flex-col gap-3", children: [_jsxs("label", { className: "flex flex-col gap-1 text-sm", children: ["Access token (caregiver, clinician or operator)", _jsx("input", { type: "password", autoComplete: "off", value: token, onChange: (e) => setT(e.target.value), className: "h-9 rounded-md border bg-card px-2", "data-testid": "token" })] }), err && _jsx("p", { role: "alert", className: "text-sm text-urgent-fg", "data-testid": "login-error", children: err }), _jsxs(Button, { type: "submit", disabled: !token || busy, "data-testid": "login", children: [_jsx(LogIn, { className: "size-4", "aria-hidden": "true" }), "Log in"] })] }) })] })] }));
}
