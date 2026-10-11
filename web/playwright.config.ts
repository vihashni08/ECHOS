import { defineConfig } from "@playwright/test"

// The e2e tests own their whole stack: a throw-away COPY of the UI dev database (data/echos_e2e.db), an API on port 8010 and a Vite server on 5180.
// They never touch data/echos.db, the UI dev copy, or the dev ports (8000 / 5173). Backend: dry-run mail, no Gemini, no PubMed.
const API = "http://127.0.0.1:8010"
const WEB = "http://localhost:5180"
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: WEB, viewport: { width: 1440, height: 900 }, trace: "off" },
  webServer: [
    {
      command: `bash -c "cd .. && scripts/make_ui_dev_copy.sh data/echos_ui_dev.db echos_e2e && ECHOS_API_PORT=8010 ECHOS_CORS_ORIGIN=${WEB} scripts/run_api.sh --dry --no-llm --demo --db data/echos_e2e.db"`,
      url: `${API}/api/sim/state`, timeout: 120_000, reuseExistingServer: false, gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
    },
    { command: `VITE_API_URL=${API} npx vite --host 127.0.0.1 --port 5180 --strictPort`, url: "http://127.0.0.1:5180", timeout: 60_000, reuseExistingServer: false },
  ],
})
