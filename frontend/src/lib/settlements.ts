// Tipos y llamadas del desglose de liquidaciones de TikTok Shop
// (backend: app/settlement_api.py y app/settlement_breakdown.py). Solo lectura.
import { authFetch, parse, query } from '@/lib/api'

export type ConceptKind = 'revenue' | 'fee' | 'shipping' | 'adjustment' | 'memo'

export interface ConceptRow {
  code: string
  name: string
  kind: ConceptKind
  /** Monto firmado (ingreso positivo, costo negativo). */
  amount: number
  /** Costo en positivo; negativo si el concepto sumó dinero (p. ej. un ajuste a favor). */
  cost: number
  pct_of_sales: number | null
  previous_cost: number
  previous_pct_of_sales: number | null
  /** Variación porcentual del costo contra el período anterior (null sin base). */
  change_pct: number | null
  change_amount: number
}

export interface MonthRow {
  month: string
  sales: number
  fees: number
  fee_pct: number | null
  concepts: Record<string, number>
}

export interface StatementRow {
  id: number
  external_id: string
  statement_date: string | null
  payout_date: string | null
  status: string | null
  sales: number
  fees: number
  fee_pct: number | null
  net_payout: number
}

export interface BreakdownResponse {
  start: string
  end: string
  previous_start: string
  previous_end: string
  summary: {
    statements: number
    sales: number
    fees: number
    fee_pct: number
    shipping: number
    net_payout: number
    biggest_concept: { code: string; name: string; cost: number; pct_of_sales: number | null } | null
    previous_fees: number
    previous_fee_pct: number
    fees_change_pct: number | null
    fee_pct_change_points: number | null
  }
  by_concept: ConceptRow[]
  by_month: MonthRow[]
  statements: StatementRow[]
}

export interface WaterfallStepRow {
  code: string
  name: string
  kind: ConceptKind
  amount: number
}

export interface WaterfallResponse {
  id: number
  external_id: string
  statement_date: string | null
  payout_date: string | null
  status: string | null
  sales: number
  fees: number
  fee_pct: number | null
  net_payout: number
  steps: WaterfallStepRow[]
}

const get = <T>(path: string) => authFetch(path).then((r) => parse<T>(r))

export const settlements = {
  breakdown: (p: { start?: string; end?: string; account?: string }) =>
    get<BreakdownResponse>(`/api/settlements/breakdown${query({ start: p.start, end: p.end, account: p.account || undefined })}`),
  waterfall: (id: number) => get<WaterfallResponse>(`/api/settlements/${id}/waterfall`),
}
