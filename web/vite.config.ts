import { readFileSync } from "node:fs"
import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// default API URL: the one documented in scripts/run_api.sh ("API: http://127.0.0.1:8000"); override with VITE_API_URL
function defaultApiUrl(): string {
  try {
    const m = readFileSync(path.resolve(import.meta.dirname, "../scripts/run_api.sh"), "utf8").match(/API: (http:\/\/127\.0\.0\.1:\d+)/)
    if (m) return m[1]
  } catch { /* fall through */ }
  return "http://127.0.0.1:8000"
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  optimizeDeps: { include: ["react-is"] },
  define: { __API_DEFAULT__: JSON.stringify(defaultApiUrl()) },
})
