import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Component } from "react";
import { Button } from "@/components/ui/button";
export default class ErrorBoundary extends Component {
    state = { error: null, key: this.props.resetKey };
    static getDerivedStateFromError(error) { return { error }; }
    static getDerivedStateFromProps(p, s) { return p.resetKey !== s.key ? { error: null, key: p.resetKey } : null; }
    render() {
        if (!this.state.error)
            return this.props.children;
        return (_jsxs("div", { role: "alert", className: "flex flex-col items-start gap-3 rounded-xl border border-urgent-line bg-urgent-bg p-4 text-urgent-fg", children: [_jsx("p", { className: "font-semibold", children: "This page hit an unexpected error." }), _jsx("p", { className: "text-sm", children: this.state.error.message }), _jsx(Button, { variant: "outline", onClick: () => this.setState({ error: null }), children: "Try again" })] }));
    }
}
