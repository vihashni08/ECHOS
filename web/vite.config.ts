import { readFileSync, realpathSync } from "node:fs"
import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, searchForWorkspaceRoot } from "vite"

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
  // node_modules may be a symlink (e.g. a git worktree sharing the main checkout's install): allow its real location too
  server: { fs: { allow: [searchForWorkspaceRoot(process.cwd()), realpathSync(path.resolve(import.meta.dirname, "node_modules"))] } },
})
