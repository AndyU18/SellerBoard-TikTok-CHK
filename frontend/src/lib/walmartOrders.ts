// Tipos y llamadas de la portada y los pedidos por surtir de Walmart (backend: app/walmart_api.py y
// app/walmart_analytics.py). Solo lectura, permiso profit.view. Los pedidos no traen datos personales.
// Importado por pages/walmart/WalmartOverview y pages/walmart/WalmartOpenOrders.
import { authFetch, parse, query } from '@/lib/api'

const get = <T>(path: string, days?: number) =>
  authFetch(`${path}${query({ days: days == null ? undefined : String(days) })}`).then((r) => parse<T>(r))

// --- pedidos por surtir -----------------------------------------------------------------------------

export interface OpenOrder {
  /** Número de compra (purchaseOrderId). */
  order_id: string
  ordered_at: string | null
  ship_by: string | null
  deliver_by: string | null
  /** Horas que faltan para el plazo de envío; negativo = vencido; null = sin plazo. */
  hours_left: number | null
  overdue: boolean
  due_soon: boolean
  status: string | null
  units: number
  sales: number
  lines: number
}

export interface OpenOrdersSummary {
  open_orders: number
  overdue: number
  due_soon: number
  no_deadline: number
  units: number
  sales: number
  sales_overdue: number
  sales_due_soon: number
  due_soon_window_hours: number
}

export interface OpenOrdersResponse {
  summary: OpenOrdersSummary
  orders: OpenOrder[]
  /** true si la respuesta salió de la caché del servidor (no de una consulta nueva a Walmart). */
  cached: boolean
  cache_ttl_seconds: number
  as_of: string
}

/** Cubetas de horas restantes para el plazo de envío. */
export type HoursBucket = 'overdue' | 'lt6' | 'h6_24' | 'h24_48' | 'gt48' | 'none'

export const HOURS_BUCKETS: { key: HoursBucket; label: string }[] = [
  { key: 'overdue', label: 'Vencido' },
  { key: 'lt6', label: '< 6 h' },
  { key: 'h6_24', label: '6-24 h' },
  { key: 'h24_48', label: '24-48 h' },
  { key: 'gt48', label: '> 48 h' },
  { key: 'none', label: 'Sin plazo' },
]

const SOON_HOURS = 6
const DAY_HOURS = 24
const TWO_DAYS_HOURS = 48

export function hoursBucket(hours: number | null): HoursBucket {
  if (hours == null) return 'none'
  if (hours < 0) return 'overdue'
  if (hours < SOON_HOURS) return 'lt6'
  if (hours < DAY_HOURS) return 'h6_24'
  if (hours < TWO_DAYS_HOURS) return 'h24_48'
  return 'gt48'
}

// --- portada ----------------------------------------------------------------------------------------

export interface OverviewKpis {
  gross_sales: number
  net_profit: number
  orders: number
  units: number
  margin: number | null
}

export interface OverviewPerformanceWindow {
  window_days: number
  ots: number | null
  otd: number | null
  vtr: number | null
  refund_rate_seller_fault: number | null
  cancellation_seller_fault: number | null
}

/** Pedidos por surtir en la portada: si `available` es falso, Walmart no respondió (`error` explica). */
export type OverviewOpenOrders = ({ available: true } & OpenOrdersSummary) | { available: false; error: string }

export interface WalmartOverviewResponse {
  days: number
  kpis: OverviewKpis
  listing_quality: { snapshot_date: string; score: number | null } | null
  seller_performance: { snapshot_date: string | null; windows: OverviewPerformanceWindow[] }
  open_orders: OverviewOpenOrders | null
  feeds_with_failures: number
  feed_failure_window_days: number
  wfs_out_of_stock_skus: number
}

export const walmartOrders = {
  openOrders: () => get<OpenOrdersResponse>('/api/walmart/open-orders'),
  overview: (days: number) => get<WalmartOverviewResponse>('/api/walmart/overview', days),
}
