import { createContext, useContext, useEffect, useState, type ReactNode } from "react"

type Theme = "light" | "dark"
const Ctx = createContext<{ theme: Theme; toggle: () => void }>({ theme: "light", toggle: () => {} })

function initial(): Theme {
  try {
    const t = localStorage.getItem("echos-theme-v2")
    if (t === "dark" || t === "light") return t
  } catch { /* storage unavailable */ }
  return "light"      // light by default, whatever the system setting; the toggle still offers dark
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(initial)
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
    try { localStorage.setItem("echos-theme-v2", theme) } catch { /* ignore */ }
  }, [theme])
  return <Ctx.Provider value={{ theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) }}>{children}</Ctx.Provider>
}
export const useTheme = () => useContext(Ctx)
