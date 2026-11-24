// Tipos y llamada de la portada de TikTok Shop (backend: GET /api/tiktok/overview en app/tiktok_extra_api.py,
// análisis en app/tiktok_extra_analytics.py). Solo lectura, permiso profit.view.
// Importado por pages/tiktok/TikTokOverview.
import { authFetch, parse, query } from '@/lib/api'

export interface TikTokOverviewKpis {
  sales: number
  net_profit: number
  orders: number
  units: number
}

export interface TikTokRejectedProducts {
  count: number
  with_sales: number
  sales_usd: number
}

export interface TikTokStuckPackages {
  count: number
  stuck_days: number
}

export interface TikTokReconciliation {
  checkable: number
  reconciled: number
  paid: number
  settled: number
  difference: number
  tolerance_usd: number
}

export interface TikTokPaymentsSummary {
  pending: number
  pending_amount: number
  reconciliation: TikTokReconciliation
}

export interface TikTokReceivable {
  unpaid_settlements: number
  unpaid_settlements_amount: number
  pending_payments: number
  pending_payments_amount: number
}

export interface TikTokOverviewResponse {
  days: number
  kpis: TikTokOverviewKpis
  rejected_products: TikTokRejectedProducts
  stuck_packages: TikTokStuckPackages
  payments: TikTokPaymentsSummary
  receivable: TikTokReceivable
  last_sync_at: string | null
}

export const tiktokOverview = {
  get: (days: number) =>
    authFetch(`/api/tiktok/overview${query({ days: String(days) })}`).then((r) => parse<TikTokOverviewResponse>(r)),
}
