// Tipos y llamada de la rentabilidad real por creador, video y LIVE
// (backend: app/creator_profit_api.py y app/creator_profit.py). Solo lectura.
import { authFetch, parse, query } from '@/lib/api'

export type ProfitLevel = 'creator' | 'video' | 'live'
export type Verdict = 'Renovar' | 'Mantener' | 'Vigilar' | 'Cortar'

export const LEVEL_LABEL: Record<ProfitLevel, string> = { creator: 'Creador', video: 'Video', live: 'LIVE' }

export interface CreatorProfitRow {
  key: string
  label: string
  creator: string | null
  orders: number
  orders_pending: number
  units: number
  sales: number
  promo: number
  platform_fee: number
  commission: number
  shipping: number
  refunds: number
  cogs: number
  sample_cost: number
  profit_before_samples: number
  profit: number
  margin: number | null
  roi: number | null
  payback_orders: number | null
  verdict: Verdict
  series: { week: string; sales: number; profit: number }[]
}

export interface CreatorProfitSummary {
  affiliate_orders: number
  affiliate_orders_pending: number
  affiliate_sales: number
  affiliate_profit_before_samples: number
  sample_cost: number
  affiliate_profit: number
  affiliate_share_of_sales: number | null
  total_sales: number
  never_sold_creators: number
  never_sold_cost: number
  verdict_counts: Record<Verdict, number>
  entities: number
}

export interface CreatorProfitResponse {
  level: ProfitLevel
  start: string | null
  end: string | null
  thresholds: { min_orders: number; renew_margin: number; keep_margin: number; sample_grace_days: number }
  summary: CreatorProfitSummary
  weeks: string[]
  total: number
  rows: CreatorProfitRow[]
  notes: string[]
}

export interface CreatorProfitParams {
  level: ProfitLevel
  start?: string
  end?: string
  q?: string
  limit?: number
  offset?: number
}

export const creatorProfit = {
  report: (p: CreatorProfitParams) => {
    const out: Record<string, string | undefined> = {}
    for (const [k, v] of Object.entries(p)) out[k] = v == null || v === '' ? undefined : String(v)
    return authFetch(`/api/creators/profit${query(out)}`).then((r) => parse<CreatorProfitResponse>(r))
  },
}
