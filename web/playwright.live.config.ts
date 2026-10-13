import { defineConfig } from "@playwright/test"

// LIVE-mode e2e: its own stack (API :8021 in `--mode live` with the labelled emulator, Vite :5191) on a FRESH throw-away database (data/echos_e2e_live.db).
// Never touches the dev ports, the UI dev copy or data/echos.db. Backend: dry-run mail, no Gemini, no PubMed.
const API = "http://127.0.0.1:8021"
const WEB = "http://localhost:5191"
export default defineConfig({
  testDir: "./e2e-live",
  timeout: 180_000,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: WEB, viewport: { width: 1440, height: 900 }, trace: "off" },
  webServer: [
    {
      command: `bash -c "cd .. && rm -f data/echos_e2e_live.db* data/echos_e2e_live.cp.sqlite* && ECHOS_API_PORT=8021 ECHOS_CORS_ORIGIN=${WEB} scripts/run_api.sh --dry --no-llm --demo --mode live --db data/echos_e2e_live.db"`,
      url: `${API}/api/live/status`, timeout: 120_000, reuseExistingServer: false, gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
    },
    { command: `VITE_API_URL=${API} npx vite --host 127.0.0.1 --port 5191 --strictPort`, url: "http://127.0.0.1:5191", timeout: 60_000, reuseExistingServer: false },
  ],
})
