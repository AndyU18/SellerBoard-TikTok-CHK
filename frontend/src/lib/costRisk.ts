// Tipos y llamada de "Utilidad en riesgo por costo faltante" (backend: app/cost_risk_api.py y app/cost_risk.py).
// Importado por pages/CostRisk. Solo lectura. El costo de los SKU sin costo es una ESTIMACIÓN.
import { authFetch, parse, query } from '@/lib/api'

export interface CostRiskSku {
  /** 1 = el SKU al que más conviene cargarle el costo primero (más ventas). */
  priority: number
  sku: string
  name: string | null
  units: number
  net_sales: number
  /** Utilidad con costo 0, tal como la ve el tablero hoy. */
  reported_profit: number
  est_unit_cost: number
  est_cogs: number
  adjusted_profit: number
  /** Utilidad reportada − ajustada (= costo estimado). */
  profit_at_risk: number
  adjusted_margin: number | null
  /** Parte de las ventas sin costo que representa este SKU (0–1). */
  sales_share: number | null
  /** Parte de las ventas sin costo que se cubre al cargar los primeros `priority` SKU (0–1). */
  cumulative_sales_share: number | null
}

export interface CostRiskSummary {
  days: number
  start: string
  end: string
  missing_skus: number
  net_sales_total: number
  net_sales_missing: number
  pct_sales_missing: number | null
  reported_profit_total: number
  reported_profit_missing: number
  pct_profit_missing: number | null
  adjusted_profit_total: number
  profit_at_risk: number
  overstated_pct: number | null
  cost_ratio: number
  cost_ratio_source: 'median' | 'fallback'
  cost_ratio_samples: number
  /** Ventas sin costo que se cubren al cargar los primeros 5 y 10 SKU. */
  coverage: { '5': number | null; '10': number | null }
  note: string
}

export interface CostRiskResponse {
  summary: CostRiskSummary
  skus: CostRiskSku[]
}

export interface CostRiskFilter {
  days: number
  channel?: string
  account?: string
}

export const costRisk = {
  report: ({ days, channel, account }: CostRiskFilter) =>
    authFetch(`/api/costs/risk${query({ days: String(days), channel: channel || undefined, account: account || undefined })}`).then((r) =>
      parse<CostRiskResponse>(r),
    ),
}
