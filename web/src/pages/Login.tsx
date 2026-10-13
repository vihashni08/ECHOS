import { useState } from "react"
import { HeartPulse, LogIn } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { client, call } from "@/lib/api"
import { setToken } from "@/lib/auth"

export default function Login() {
  const qc = useQueryClient()
  const [token, setT] = useState("")
  const [err, setErr] = useState("")
  const [busy, setBusy] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(""); setBusy(true)
    try { await call(client.POST("/api/auth/login", { body: { token } })); setToken(token); setT(""); await qc.invalidateQueries() }
    catch (x) { setErr((x as Error).message) } finally { setBusy(false) }
  }
  return (
    <main id="main" className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 p-4">
      <div className="flex items-center gap-2"><HeartPulse className="size-7 text-primary" aria-hidden="true" /><div><h1 className="text-xl font-semibold">ECHOS</h1><p className="text-sm text-muted-foreground">Decision support, not a diagnosis</p></div></div>
      <Card><CardHeader><CardTitle>Log in</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={submit} className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm">Access token (caregiver, clinician or operator)
              <input type="password" autoComplete="off" value={token} onChange={(e) => setT(e.target.value)} className="h-9 rounded-md border bg-card px-2" data-testid="token" />
            </label>
            {err && <p role="alert" className="text-sm text-urgent-fg" data-testid="login-error">{err}</p>}
            <Button type="submit" disabled={!token || busy} data-testid="login"><LogIn className="size-4" aria-hidden="true" />Log in</Button>
          </form>
        </CardContent></Card>
    </main>
  )
}
