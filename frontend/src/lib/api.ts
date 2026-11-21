import { supabase } from '@/lib/supabase'

export type Channel = 'amazon' | 'tiktok_shop' | 'walmart' | 'shopify'
export type GroupBy = 'channel' | 'product' | 'channel_product' | 'account'

export const CHANNELS: Channel[] = ['amazon', 'tiktok_shop', 'walmart', 'shopify']
export const CHANNEL_LABEL: Record<Channel, string> = {
  amazon: 'Amazon',
  tiktok_shop: 'TikTok Shop',
  walmart: 'Walmart',
  shopify: 'Shopify',
}
export const CHANNEL_COLOR: Record<Channel, string> = {
  amazon: '#ff9900',
  tiktok_shop: '#fe2c55',
  walmart: '#0071ce',
  shopify: '#5e8e3e',
}

export interface ProfitRow {
  channel?: Channel
  account?: string
  sku?: string
  product_name?: string | null
  period?: string
  units: number
  gross_sales: number
  promo: number
  platform_fees: number
  shipping_cost: number
  refund_amount: number
  ad_spend: number
  cogs: number
  allocated_expenses: number
  net_profit: number
  unit_profit: number | null
  margin: number | null
  roi: number | null
  tacos: number | null
}

export interface ProfitResponse {
  group_by: GroupBy
  rows: ProfitRow[]
  totals: ProfitRow | null
}

export interface Product {
  id: number
  sku: string
  name: string | null
  cogs_unit: number
  has_cost: boolean
}

export interface CostHistoryEntry {
  valid_from: string
  amount: number
  component: string
}

export type AbcClass = 'A' | 'B' | 'C' | 'loss'

export interface ParetoRow {
  sku: string
  product_name: string | null
  units: number
  gross_sales: number
  net_profit: number
  margin: number | null
  share: number | null
  cumulative_share: number | null
  abc_class: AbcClass
}

export interface ParetoResponse {
  metric: 'profit' | 'sales'
  rows: ParetoRow[]
  summary: Partial<Record<AbcClass, { products: number; value: number }>>
}

export interface PromotionRow {
  channel: Channel
  sku: string
  product_name: string | null
  units: number
  gross_sales: number
  /** Descuento que paga el vendedor (ya resta de la utilidad). */
  promo: number
  /** Subsidio de la plataforma (no resta utilidad). */
  platform_discount: number
  list_amount: number
  net_profit: number | null
  margin: number | null
  promo_rate: number | null
  discount_depth: number | null
  subsidy_share: number | null
  loses_money: boolean
  deep_discount: boolean
}

export interface PromotionTotals {
  units: number
  gross_sales: number
  promo: number
  platform_discount: number
  list_amount: number
  promo_rate: number | null
  discount_depth: number | null
  subsidy_share: number | null
}

export interface PromotionsResponse {
  rows: PromotionRow[]
  totals: PromotionTotals | null
}

export interface DaypartCell {
  /** 0 = lunes … 6 = domingo. */
  weekday: number
  hour: number
  orders: number
  units: number
  gross_sales: number
}

export interface DaypartingResponse {
  timezone: string
  coverage: { with_time: number; total: number }
  cells: DaypartCell[]
  best_hours: { hour: number; orders: number; units: number; gross_sales: number }[]
}

export interface SettlementRow {
  channel: Channel
  external_id: string
  statement_date: string | null
  payout_date: string | null
  status: string | null
  gross_amount: number
  fees_amount: number
  shipping_amount: number
  adjustment_amount: number
  net_payout: number
}

export interface CashflowResponse {
  summary: { received: number; pending: number; failed: number; statements: number }
  by_month: { month: string; received: number; pending: number }[]
  rows: SettlementRow[]
}

export interface ReturnsResponse {
  summary: {
    returns: number
    returns_open: number
    return_refund: number
    returned_units: number
    units_sold: number
    return_rate: number | null
    cancellations: number
    cancel_refund: number
    seller_cancellations: number
  }
  by_reason: { kind: 'return' | 'cancellation'; reason: string; cases: number; refund_total: number }[]
  by_product: {
    sku: string
    product_name: string | null
    returned_units: number
    returned_refund: number
    cancelled_units: number
    units_sold: number
    return_rate: number | null
  }[]
  recent: {
    external_id: string
    kind: 'return' | 'cancellation'
    case_type: string | null
    status: string | null
    reason: string | null
    initiated_by: string | null
    external_order_id: string
    case_date: string
    refund_total: number
  }[]
}

export interface LogisticsResponse {
  summary: {
    shipments: number
    cancelled: number
    sample_orders: number
    handling_hours_avg: number | null
    handling_hours_median: number | null
    transit_days_avg: number | null
    transit_days_median: number | null
    shipped_on_time: number
    shipped_late: number
    on_time_rate: number | null
    overdue_now: number
  }
  by_status: { status: string; orders: number }[]
  by_carrier: { carrier: string; shipments: number; delivered: number; transit_days_avg: number | null }[]
}

export interface SamplesResponse {
  summary: {
    sample_orders: number
    sample_units: number
    creators: number
    creators_with_sales: number
    creators_without_sales: number
    sample_cost: number
    cost_not_in_profit: number
    affiliate_profit: number
    net: number
    roi: number | null
    orders_pending: number
  }
  creators: {
    creator: string
    sample_orders: number
    sample_units: number
    sample_cost: number
    cost_not_in_profit: number
    first_sample: string | null
    affiliate_orders: number
    affiliate_units: number
    affiliate_gmv: number
    commission: number
    affiliate_profit: number
    orders_pending: number
    net: number
    roi: number | null
  }[]
  applications_by_status: { status: string; applications: number }[]
}

export interface FunnelStep {
  impressions: number
  page_views: number
  visitors: number
  buyers: number
  orders: number
  units: number
  gmv: number
  ctr: number | null
  conversion: number | null
}

export interface FunnelResponse {
  days: number
  totals: FunnelStep & { aov: number | null }
  by_source: (FunnelStep & { source: 'VIDEO' | 'LIVE' | 'PRODUCT_CARD'; gmv_share: number | null })[]
  daily: {
    day: string
    impressions: number
    page_views: number
    orders: number
    buyers: number
    gmv: number
    ctr: number | null
    conversion: number | null
  }[]
}

export interface LiveSessionRow {
  external_id: string
  username: string | null
  title: string | null
  start_at: string
  duration_min: number | null
  gmv: number
  orders: number
  customers: number
  units: number
  products_added: number
  click_to_order_rate: number | null
  gmv_per_hour: number | null
}

export interface LiveResponse {
  summary: {
    sessions: number
    sessions_with_sales: number
    gmv: number
    orders: number
    customers: number
    units: number
    avg_gmv_per_session: number | null
    avg_duration_min: number | null
    gmv_per_hour: number | null
    top5_share: number | null
  }
  top_sessions: LiveSessionRow[]
  by_creator: { username: string; sessions: number; gmv: number; orders: number }[]
}

export interface CreativesResponse {
  summary: {
    contents: number
    creators: number
    orders: number
    gmv: number
    commission: number
    profit: number
    top10_share: number | null
    losing_contents: number
    orders_pending: number
  }
  by_type: { content_type: string; contents: number; orders: number; gmv: number; profit: number }[]
  contents: {
    content_type: string
    content_id: string
    creator: string | null
    url: string | null
    orders: number
    orders_pending: number
    units: number
    gmv: number
    commission: number
    profit: number
    margin: number | null
    first_order: string
    last_order: string
    top_sku: string | null
    sampled: boolean
    /** Vistas y CTR del video en los últimos 60 días (null si no es un video o no está entre los 300 con más ventas). */
    views: number | null
    ctr: number | null
  }[]
}

export interface Filters {
  start?: string
  end?: string
  channel?: Channel | ''
  /** Nombre de la tienda (cuenta); vacío = todas. */
  account?: string
}

export interface AccountRow {
  id: number
  channel: Channel
  name: string
}

export interface ImportResult {
  inserted: number
  updated: number
  accounts_created: number
  products_created: number
  /** Solo en el reporte de etiquetas (Redo). */
  matched_orders?: number
  unmatched_orders?: number
  stats?: { orders: number; transactions_excluded: number; unassigned_charges: number; unassigned_total: number }
}

export class ApiError extends Error {
  problems: string[]
  constructor(message: string, problems: string[] = []) {
    super(message)
    this.problems = problems
  }
}


/** fetch con el token de la sesión de Supabase. Si el backend responde 401, cierra la sesión. */
async function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const { data } = await supabase.auth.getSession()
  const headers = new Headers(init.headers)
  if (data.session) headers.set('Authorization', `Bearer ${data.session.access_token}`)
  const res = await fetch(input, { ...init, headers })
  if (res.status === 401) await supabase.auth.signOut()
  return res
}

function query(params: Record<string, string | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v)
  const s = q.toString()
  return s ? `?${s}` : ''
}

async function parse<T>(res: Response): Promise<T> {
  if (res.ok) return res.json() as Promise<T>
  let problems: string[] = []
  let message = `Error ${res.status}`
  try {
    const body = await res.json()
    const detail = body?.detail
    if (detail?.problems) problems = detail.problems
    else if (typeof detail === 'string') message = detail
  } catch {
    /* respuesta sin JSON */
  }
  throw new ApiError(problems[0] ?? message, problems)
}

export const api = {
  profit: (groupBy: GroupBy, f: Filters) =>
    authFetch(`/api/profit${query({ group_by: groupBy, start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then(
      (r) => parse<ProfitResponse>(r),
    ),
  timeseries: (interval: 'day' | 'month', f: Filters) =>
    authFetch(
      `/api/profit/timeseries${query({ interval, start: f.start, end: f.end, channel: f.channel, account: f.account })}`,
    ).then((r) => parse<{ interval: string; rows: ProfitRow[] }>(r)),
  pareto: (metric: 'profit' | 'sales', f: Filters) =>
    authFetch(`/api/profit/pareto${query({ metric, start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then((r) =>
      parse<ParetoResponse>(r),
    ),
  promotions: (f: Filters) =>
    authFetch(`/api/promotions${query({ start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then((r) =>
      parse<PromotionsResponse>(r),
    ),
  dayparting: (f: Filters) =>
    authFetch(`/api/dayparting${query({ start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then((r) =>
      parse<DaypartingResponse>(r),
    ),
  returns: (f: Filters) =>
    authFetch(`/api/returns${query({ start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then((r) =>
      parse<ReturnsResponse>(r),
    ),
  logistics: (f: Filters) =>
    authFetch(`/api/logistics${query({ start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then((r) =>
      parse<LogisticsResponse>(r),
    ),
  samples: (f: Filters) =>
    authFetch(`/api/samples${query({ start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then((r) => parse<SamplesResponse>(r)),
  funnel: (f: Filters) =>
    authFetch(`/api/funnel${query({ start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then((r) => parse<FunnelResponse>(r)),
  live: (f: Filters) =>
    authFetch(`/api/live${query({ start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then((r) => parse<LiveResponse>(r)),
  creatives: (f: Filters) =>
    authFetch(`/api/creatives${query({ start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then((r) => parse<CreativesResponse>(r)),
  cashflow: (f: Filters) =>
    authFetch(`/api/cashflow${query({ start: f.start, end: f.end, channel: f.channel, account: f.account })}`).then((r) => parse<CashflowResponse>(r)),
  accounts: () => authFetch('/api/accounts').then((r) => parse<AccountRow[]>(r)),
  products: () => authFetch('/api/products').then((r) => parse<Product[]>(r)),
  cogsHistory: (id: number) =>
    authFetch(`/api/products/${id}/cogs-history`).then((r) => parse<CostHistoryEntry[]>(r)),
  updateCogs: (id: number, cogs_unit: number, valid_from?: string) =>
    authFetch(`/api/products/${id}/cogs`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cogs_unit, valid_from: valid_from || null }),
    }).then((r) => parse<{ id: number; cogs_unit: number }>(r)),
  upload: (kind: 'orders' | 'ad-spend' | 'cogs' | 'shipping-labels', file: File) => {
    const body = new FormData()
    body.append('file', file)
    return authFetch(`/api/import/${kind}`, { method: 'POST', body }).then((r) => parse<ImportResult>(r))
  },
  syncRedo: () => authFetch('/api/sync/redo', { method: 'POST' }).then((r) => parse<ImportResult>(r)),
  syncWalmart: (days: number) =>
    authFetch(`/api/sync/walmart?days=${days}`, { method: 'POST' }).then((r) =>
      parse<{ days: number; stats: Record<string, number>; imported: ImportResult }>(r),
    ),
  syncTikTok: (days: number) =>
    authFetch(`/api/sync/tiktok-shop?days=${days}`, { method: 'POST' }).then((r) =>
      parse<{ days: number; stats: Record<string, number>; imported: ImportResult }>(r),
    ),
}
