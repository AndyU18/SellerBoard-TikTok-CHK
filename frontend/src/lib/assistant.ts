// Tipos y llamadas del asistente con IA (backend: app/assistant_api.py y app/explain.py).
import { authFetch, parse, query, type Channel } from '@/lib/api'

export interface ExplainTotals {
  units: number
  net_profit: number
  gross_sales: number
}

export interface ExplainPeriod {
  start: string
  end: string
  totals: ExplainTotals
}

export interface ExplainComponent {
  key: string
  label: string
  current: number
  previous: number
  delta: number
  /** Efecto en la utilidad, con signo: negativo = restó utilidad. */
  effect: number
}

export interface ExplainProduct {
  sku: string
  product_name: string | null
  units_current: number
  units_previous: number
  profit_current: number
  profit_previous: number
  delta: number
  volume_effect: number
  rate_effect: number
}

export interface ExplainChannel {
  channel: string
  profit_current: number
  profit_previous: number
  delta: number
  sales_current: number
  sales_previous: number
}

export interface ProfitExplanation {
  days: number
  channel: string | null
  current: ExplainPeriod
  previous: ExplainPeriod
  change: {
    profit_current: number
    profit_previous: number
    profit_delta: number
    profit_pct: number | null
    units_current: number
    units_previous: number
  }
  components: ExplainComponent[]
  products: ExplainProduct[]
  channels: ExplainChannel[]
  caveats: string[]
  headline: string
}

export interface AssistantStatus {
  configured: boolean
  model: string
}

export interface AssistantAnswer {
  answer: string
  model: string
  usage: { input_tokens?: number; output_tokens?: number }
  context_chars: number
}

export interface WeeklyReport {
  ai: boolean
  report: string
  model: string | null
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const assistantApi = {
  status: () => authFetch('/api/assistant/status').then((r) => parse<AssistantStatus>(r)),
  explain: (days: number, channel?: Channel) =>
    authFetch(`/api/profit/explain${query({ days: String(days), channel })}`).then((r) =>
      parse<ProfitExplanation>(r),
    ),
  ask: (question: string, days: number) =>
    authFetch('/api/assistant/ask', json({ question, days })).then((r) => parse<AssistantAnswer>(r)),
  weeklyReport: () => authFetch('/api/assistant/weekly-report').then((r) => parse<WeeklyReport>(r)),
}
