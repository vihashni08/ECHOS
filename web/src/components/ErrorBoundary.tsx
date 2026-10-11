import { Component, type ReactNode } from "react"
import { Button } from "@/components/ui/button"

export default class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null; key?: string }> {
  state = { error: null as Error | null, key: this.props.resetKey }
  static getDerivedStateFromError(error: Error) { return { error } }
  static getDerivedStateFromProps(p: { resetKey?: string }, s: { key?: string }) { return p.resetKey !== s.key ? { error: null, key: p.resetKey } : null }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div role="alert" className="flex flex-col items-start gap-3 rounded-xl border border-urgent-line bg-urgent-bg p-4 text-urgent-fg">
        <p className="font-semibold">This page hit an unexpected error.</p>
        <p className="text-sm">{this.state.error.message}</p>
        <Button variant="outline" onClick={() => this.setState({ error: null })}>Try again</Button>
      </div>
    )
  }
}
