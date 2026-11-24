// Tipos y llamada de la política de muestras (backend: app/sample_policy_api.py y app/sample_policy.py).
// Solo lectura: la regla se simula sobre el historial, nada se envía ni se corta desde aquí.
import { authFetch, parse, query } from '@/lib/api'

export type Recommendation = 'Seguir enviando' | 'Enviar con tope' | 'Pausar' | 'Cortar'

export const RECOMMENDATIONS: Recommendation[] = ['Seguir enviando', 'Enviar con tope', 'Pausar', 'Cortar']

/** Límites que valida el backend (ge/le del endpoint); la pantalla los usa para no pedir valores inválidos. */
export const LIMITS = {
  maxSamples: { min: 0, max: 20, default: 2 },
  maxDays: { min: 1, max: 365, default: 30 },
} as const

export interface ScorePart {
  key: 'conversion' | 'profit' | 'speed' | 'consistency'
  label: string
  /** Puntos máximos del componente (los pesos suman 100). */
  weight: number
  /** Cumplimiento 0-1. */
  value: number
  points: number
}

export interface SamplePolicyRow {
  creator: string
  samples: number
  sample_cost: number
  avg_sample_cost: number
  orders: number
  sales: number
  profit_before_samples: number
  profit: number
  first_sample: string
  first_sale: string | null
  days_to_first_sale: number | null
  days_since_first_sample: number
  content_type: string | null
  active_weeks: number
  /** Órdenes por muestra enviada. */
  sample_conversion: number | null
  score: number
  score_parts: ScorePart[]
  recommendation: Recommendation
  reason: string
  suggested_samples: number
  suggested_budget_usd: number
}

export interface PolicySimulation {
  max_samples_without_sale: number
  max_days_to_first_sale: number
  creators_stopped: number
  samples_saved: number
  savings: number
  false_positives: number
  lost_sales: number
  lost_profit: number
  net_benefit: number
}

export interface SamplePolicySummary {
  creators: number
  recommendation_counts: Record<Recommendation, number>
  to_pause: number
  sample_cost: number
  never_sold_creators: number
  never_sold_cost: number
  estimated_savings: number
  false_positives: number
  lost_sales: number
  lost_profit: number
  net_benefit: number
  profit_before_samples: number
  profit_after_samples: number
  profit_with_policy: number
}

export interface SamplePolicyResponse {
  start: string | null
  end: string | null
  today: string
  params: { max_samples_without_sale: number; max_days_to_first_sale: number }
  weights: Record<ScorePart['key'], number>
  thresholds: { send_score: number; cap_score: number }
  summary: SamplePolicySummary
  simulation: PolicySimulation
  curve: { cap: number; savings: number; false_positives: number; lost_profit: number; net_benefit: number }[]
  histogram: { from: number; to: number; count: number }[]
  rows: SamplePolicyRow[]
  notes: string[]
}

export interface SamplePolicyParams {
  start?: string
  end?: string
  max_samples_without_sale?: number
  max_days_to_first_sale?: number
}

export const samplePolicy = {
  report: (p: SamplePolicyParams) => {
    const out: Record<string, string | undefined> = {}
    for (const [k, v] of Object.entries(p)) out[k] = v == null || v === '' ? undefined : String(v)
    return authFetch(`/api/samples/policy${query(out)}`).then((r) => parse<SamplePolicyResponse>(r))
  },
}
