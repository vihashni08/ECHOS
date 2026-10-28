import { useMetrics } from "./queries"

/** Typed reads of reports/metrics/v2_*.json (served by /api/system/metrics). Aggregates only. */
export type Res = { n: number; positives: number; prevalence: number; pr_auc: number; roc_auc: number; brier: number
  at_budget?: { alerts: number; caught: number; positives: number; recall: number; precision: number; false_alerts_per_patient_week: number }
  ci95?: { pr_auc?: [number, number]; roc_auc?: [number, number]; recall?: [number, number]; precision?: [number, number] } }
export type Diff = { diff: number; ci95: [number, number]; better: boolean }
export type Policy = { reviews: number; agitation_followed_up: number; vitals_anticipated: number; events_per_review: number; max_days_without_review: number; patients_never_reviewed: number; patients: number }
export type Agitation = { data: { activity_events: number; windows: number; patients: number; patients_with_episodes: number; positives: number; prevalence: number; patient_weeks: number }
  results: Record<string, Res>; ours_minus_baseline_pr_auc: Record<string, Diff>; per_slot: Record<string, { windows: number; positives: number; pr_auc: number }>
  calibration_deciles: { mean_prob: number; observed_rate: number; n: number }[]; robustness?: Record<string, { pr_auc: number; labels_removed: number }> }
export type Vitals = { task: string; cohort: { patients: number; patient_days: number; positives: number; prevalence: number; event_days: number }
  results: Record<string, Res>; ours_minus_baseline_pr_auc: Record<string, Diff>; new_patients: Record<string, Record<string, number>>
  calibration_deciles: { mean_forecast: number; observed_rate: number; n: number }[] }
export type Allocation = { priority: string; setup: Record<string, unknown>; by_k: Record<string, Record<string, Policy>> }
export type Audit = { q1_bp_labels_vs_thresholds: { rule: string; days_crossing: number; share_labelled: number }[]
  q4_new_episode_onsets: { onsets: number; prevalence: number; logistic_pr_auc: number; label_rate_pr_auc: number }
  q5_alert_rules: Record<string, { share_of_reading_days: number; per_patient_week: number; share_confirmed_by_study: number }>; conclusions: string[] }
type Latency = { event_to_queued_median: number; event_to_queued_max: number }
export type Runtime = { reference: { events: number; days_scored: number; alerts: number; urgent_latency_s: Latency; reviews: number; days_allocated: number; unconsumed_messages: number }
  kill_run: { events: number; days_scored: number; alerts: number; urgent_latency_s: Latency; reviews: number }
  kill_vs_reference: { lost_alerts: number; extra_alerts: number; identical_review_picks: boolean; identical_case_statuses: boolean; same_days_scored: boolean } }

export type AllMetrics = { agitation?: Agitation; vitals?: Vitals; allocation?: Allocation; audit?: Audit; runtime?: Runtime }

export function useResults() {
  const m = useMetrics()
  const f = (m.data?.files ?? {}) as Record<string, unknown>
  const data: AllMetrics = { agitation: f.v2_agitation as Agitation, vitals: f.v2_vitals as Vitals, allocation: f.v2_allocation as Allocation,
    audit: f.v2_label_audit as Audit, runtime: f.v2_runtime as Runtime }
  return { ...m, results: data }
}

export const AG_MAIN = "ECHOS agitation detector"
export const VI_MAIN = "ECHOS: personal Bayesian rate"
export const f2 = (v?: number | null, d = 2) => (v == null || Number.isNaN(v) ? "n/a" : v.toFixed(d))
export const pc = (v?: number | null, d = 0) => (v == null ? "n/a" : `${(v * 100).toFixed(d)}%`)
export const ciTxt = (c?: [number, number], d = 2) => (c ? `${c[0].toFixed(d)}–${c[1].toFixed(d)}` : "n/a")
