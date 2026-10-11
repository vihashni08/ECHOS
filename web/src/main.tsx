import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router-dom"
import App from "./App"
import { EventsProvider } from "./lib/events"
import { ThemeProvider } from "./lib/theme"
import "./index.css"

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 2000, refetchOnWindowFocus: false } } })

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={qc}>
        <EventsProvider>
          <BrowserRouter><App /></BrowserRouter>
        </EventsProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
