// Tipos y llamadas del apartado Redo (backend: app/redo_api.py y app/redo_analytics.py).
// Importado por pages/RedoOverview, RedoCosts, RedoBilling y RedoCharges. Solo lectura.
import { authFetch, parse, query } from '@/lib/api'
import type { Preset } from '@/lib/format'

type Params = Record<string, string | number | undefined | null>

function qs(p: Params): string {
  const out: Record<string, string | undefined> = {}
  for (const [k, v] of Object.entries(p)) out[k] = v == null || v === '' ? undefined : String(v)
  return query(out)
}

/** Días que pide la API Redo (7 a 365) a partir del período global; 'Todo' = 365. */
export function daysFromPreset(preset: Preset, today: Date = new Date()): number {
  if (preset === '30d') return 30
  if (preset === '90d') return 90
  if (preset === 'ytd') {
    const jan1 = new Date(today.getFullYear(), 0, 1)
    const d = Math.floor((today.getTime() - jan1.getTime()) / 86_400_000) + 1
    return Math.min(365, Math.max(7, d))
  }
  return 365
}

const get = <T>(path: string, p: Params = {}) => authFetch(`${path}${qs(p)}`).then((r) => parse<T>(r))

export interface RedoKpis {
  net_cost: number
  labels: number
  surcharges: number
  voids: number
  card_fees: number
  claim_fees: number
  claim_payouts: number
  adjustments: number
  orders: number
  avg_cost_per_order: number | null
  median_cost_per_order: number | null
  surcharge_rate: number | null
  surcharge_orders: number
  surcharge_orders_pct: number | null
  relabel_orders: number
  void_rate: number | null
  unassigned_cost: number
}

export type RedoOrigin = 'tiktok_shop' | 'walmart' | 'tiktok_pendiente' | 'shopify' | 'otro'

export interface RedoOverviewResponse {
  days: number
  window: { start: string; end: string }
  last_charge: string | null
  kpis: RedoKpis
  by_subtype: { subtype: string; label: string; count: number; amount: number; is_cost: boolean }[]
  surcharge_reasons: { reason: string; count: number; amount: number }[]
  by_month: {
    month: string
    labels: number
    surcharges: number
    voids: number
    fees: number
    net: number
    orders: number
    avg_per_order: number | null
  }[]
  by_day: { date: string; net: number }[]
  coverage: { origin: RedoOrigin; label: string; orders: number; amount: number }[]
}

export interface RedoCostBucket {
  key: string
  orders: number
  cost: number
  net_sales: number
  units: number
  avg_cost: number | null
  cost_per_unit: number | null
  share_of_sales: number | null
}

export interface RedoOrderRow {
  order: string
  channel: RedoOrigin
  state: string | null
  date: string
  cost: number
  labels: number
  voids: number
  surcharge: number
  net_sales: number | null
}

export interface RedoCostsResponse {
  days: number
  window: { start: string; end: string }
  matched_orders: number
  unmatched_orders: number
  by_channel: RedoCostBucket[]
  by_state: RedoCostBucket[]
  distribution: { bucket: string; orders: number }[]
  top_orders: RedoOrderRow[]
  relabeled_orders: RedoOrderRow[]
}

export interface RedoBill {
  id: string | number
  type: string | null
  status: string | null
  period_start: string | null
  period_end: string | null
  charge_total: number
  opening_balance: number
  closing_balance: number
}

export interface RedoBillingResponse {
  bills: RedoBill[]
  topups: {
    count: number
    total: number
    average: number | null
    last_at: string | null
    last_amount: number | null
    avg_days_between: number | null
    failed: number
    card_fees: number
    recent: { created_at: string; amount: number; status: string | null }[]
  }
  burn: { spend_30d: number; daily: number; days_per_topup: number | null }
  by_month: { month: string; charges: number; topups: number; gap: number }[]
}

export interface RedoChargeRow {
  id: string | number
  created_at: string
  order_date: string | null
  order: string | null
  subtype: string
  label: string
  amount: number
  note: string | null
  is_cost: boolean
}

export interface RedoChargesResponse {
  total: number
  amount: number
  /** Resumen de todo el filtro (no solo de la página). */
  by_subtype: { subtype: string; label: string; count: number; amount: number; is_cost: boolean }[]
  by_day: { date: string; amount: number }[]
  subtypes: { value: string; label: string }[]
  rows: RedoChargeRow[]
}

export interface RedoChargesFilter {
  subtype?: string
  q?: string
  start?: string
  end?: string
  limit?: number
  offset?: number
}

export interface RedoCorrectionReason {
  reason: string
  count: number
  amount: number
}

export interface RedoSkuCorrection {
  sku: string
  name: string | null
  shipped_orders: number
  orders_with_correction: number
  correction_rate: number
  correction_amount: number
  avg_correction: number | null
  label_cost: number
  correction_pct_of_labels: number | null
  top_reasons: RedoCorrectionReason[]
  flag: boolean
  suggestion: string | null
}

export interface RedoCorrectionsResponse {
  days: number
  window: { start: string; end: string }
  thresholds: { min_orders: number; min_rate: number }
  summary: {
    total_corrections: number
    correction_count: number
    flagged_skus: number
    flagged_amount: number
    label_cost: number
    pct_of_labels: number | null
    reasons: RedoCorrectionReason[]
    unattributed: { amount: number; count: number }
  }
  skus: RedoSkuCorrection[]
}

export const redo = {
  carrierCorrections: (days: number) => get<RedoCorrectionsResponse>('/api/redo/carrier-corrections', { days }),
  overview: (days: number) => get<RedoOverviewResponse>('/api/redo/overview', { days }),
  costs: (days: number) => get<RedoCostsResponse>('/api/redo/costs', { days }),
  billing: () => get<RedoBillingResponse>('/api/redo/billing'),
  charges: (f: RedoChargesFilter) => get<RedoChargesResponse>('/api/redo/charges', { ...f }),
}
