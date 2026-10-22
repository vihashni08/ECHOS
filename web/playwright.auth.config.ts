import { defineConfig } from "@playwright/test"

// AUTH e2e: its own stack (API :8031 with --auth, Vite :5201), fresh database data/echos_e2e_auth.db. The tokens are throw-away test values set here.
const API = "http://127.0.0.1:8031"
const WEB = "http://localhost:5201"
export default defineConfig({
  testDir: "./e2e-auth",
  timeout: 90_000,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: WEB, viewport: { width: 1440, height: 900 }, trace: "off" },
  webServer: [
    {
      command: `bash -c "cd .. && rm -f data/echos_e2e_auth.db* data/echos_e2e_auth.cp.sqlite* && ECHOS_TOKEN_CAREGIVER=e2e-care ECHOS_TOKEN_CLINICIAN=e2e-clin ECHOS_TOKEN_OPERATOR=e2e-oper ECHOS_API_PORT=8031 ECHOS_CORS_ORIGIN=${WEB} scripts/run_api.sh --dry --demo --auth --db data/echos_e2e_auth.db --days 3"`,
      url: `${API}/api/auth/me`, timeout: 120_000, reuseExistingServer: false, gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
    },
    { command: `VITE_API_URL=${API} npx vite --host 127.0.0.1 --port 5201 --strictPort`, url: "http://127.0.0.1:5201", timeout: 60_000, reuseExistingServer: false },
  ],
})
