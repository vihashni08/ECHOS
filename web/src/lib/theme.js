import { jsx as _jsx } from "react/jsx-runtime";
import { createContext, useContext, useEffect, useState } from "react";
const Ctx = createContext({ theme: "light", toggle: () => { } });
function initial() {
    try {
        const t = localStorage.getItem("echos-theme-v2");
        if (t === "dark" || t === "light")
            return t;
    }
    catch { /* storage unavailable */ }
    return "light"; // light by default, whatever the system setting; the toggle still offers dark
}
export function ThemeProvider({ children }) {
    const [theme, setTheme] = useState(initial);
    useEffect(() => {
        document.documentElement.classList.toggle("dark", theme === "dark");
        try {
            localStorage.setItem("echos-theme-v2", theme);
        }
        catch { /* ignore */ }
    }, [theme]);
    return _jsx(Ctx.Provider, { value: { theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) }, children: children });
}
export const useTheme = () => useContext(Ctx);
