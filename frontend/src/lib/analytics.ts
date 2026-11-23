// Tipos y llamadas del análisis avanzado (backend: app/analytics_api.py).
import { authFetch, parse, query, type Channel } from '@/lib/api'

type Params = Record<string, string | number | undefined | null>

function qs(p: Params): string {
  const out: Record<string, string | undefined> = {}
  for (const [k, v] of Object.entries(p)) out[k] = v == null || v === '' ? undefined : String(v)
  return query(out)
}

export interface Scope {
  channel?: Channel | ''
  account?: string
}

// ---------------------------------------------------------------------------- precios

export type FloorStatus = 'loss' | 'below_target' | 'ok' | 'headroom' | 'no_cost' | 'insufficient'

export interface FloorRow {
  channel: Channel
  sku: string
  product_name: string | null
  units: number
  list_price: number | null
  realized_price: number | null
  promo_rate: number
  cogs_unit: number
  shipping_unit: number
  fee_rate: number
  refund_rate: number
  unit_profit: number | null
  margin: number | null
  break_even_price: number | null
  target_price: number | null
  suggested_list_price: number | null
  max_discount_break_even: number | null
  max_discount_target: number | null
  gap_to_target: number | null
  profit_gap_total: number
  status: FloorStatus
}

export interface PriceFloorResponse {
  days: number
  target_margin: number
  min_units: number
  summary: { rows: number; profit_gap_total: number } & Record<FloorStatus, number>
  rows: FloorRow[]
}

export interface ParityRow {
  base_sku: string
  product_name: string | null
  channels: {
    channel: Channel
    skus: string[]
    units: number
    realized_price: number
    unit_profit: number
    margin: number | null
    has_cost: boolean
  }[]
  price_gap: number
  cheapest_channel: Channel
  priciest_channel: Channel
  best_profit_channel: Channel
  profit_gap_unit: number
  flag: 'gap' | 'ok'
  complete_costs: boolean
}

export interface ParityResponse {
  days: number
  threshold: number
  summary: { products: number; with_gap: number }
  rows: ParityRow[]
}

// ---------------------------------------------------------------------------- aranceles

export interface TariffResponse {
  days: number
  extra_duty: number
  has_receipts: boolean
  summary: { skus: number; profit_now: number; profit_new: number; profit_change: number; turn_negative: number }
  rows: {
    channel: Channel
    sku: string
    product_name: string | null
    units: number
    fob: number
    duty_rate: number
    freight_rate: number
    landed_unit: number
    delta_unit: number
    profit_now: number
    profit_new: number
    margin_now: number
    margin_new: number
    realized_price: number | null
    price_increase: number | null
    turns_negative: boolean
  }[]
  monthly: {
    month: string
    containers: number
    goods: number
    duties: number
    freight: number
    duty_rate: number | null
    freight_rate: number | null
    landed_rate: number | null
  }[]
  products: {
    sku: string
    received_date: string
    fob: number
    duty: number
    freight: number
    landed: number
    duty_rate: number
    freight_rate: number
  }[]
}

export interface LandedImportResult {
  kind: 'container_report' | 'flat_costs'
  rows_read: number
  receipts_inserted?: number
  receipts_updated?: number
  products_costed?: number
  cost_changes?: number
  skus_without_product?: number
  skus_without_product_sample?: string[]
}

// ---------------------------------------------------------------------------- operación

export type ReorderStatus = 'late' | 'order_now' | 'ok' | 'overstock' | 'dead' | 'no_demand'

export interface ReorderResponse {
  params: { lead_time_days: number; safety_days: number; cover_days: number; demand_multiplier: number; window_days: number }
  summary: {
    skus: number
    units_to_order: number
    order_cost: number
    capital_in_stock: number
    capital_excess: number
    capital_dead: number
    without_cost: number
  } & Record<ReorderStatus, number>
  rows: {
    account: string
    channel: Channel
    sku: string
    product_name: string | null
    published_status: string | null
    avail_to_sell: number
    daily_units: number
    days_of_cover: number | null
    reorder_point: number
    order_qty: number
    order_by: string | null
    days_late: number
    unit_cost: number
    has_cost: boolean
    stock_value: number
    order_cost: number
    excess_value: number
    status: ReorderStatus
  }[]
}

export interface CashProjectionResponse {
  params: { horizon_days: number; lookback_days: number }
  channels_covered: string[]
  lag_days: number
  lag_measured: boolean
  daily_rate: number
  summary: {
    pending_total: number
    known_in_horizon: number
    projected_in_horizon: number
    expected_in_horizon: number
    received_30d: number
    profit_30d: number
    cash_gap_30d: number
  }
  series: { date: string; known: number; projected: number; cumulative: number }[]
  note: string | null
}

export type LabelKind = 'order' | 'sample' | 'shipment_without_order' | 'unmatched'

export interface SettlementItem {
  channel: Channel
  external_id: string
  statement_date: string | null
  status: string | null
  net_payout: number
}

export interface ReconciliationResponse {
  summary: {
    unassigned_labels_total: number
    unassigned_labels_count: number
    zero_fee_lines: number
    high_fee_lines: number
    fee_drift_channels: number
    failed_payments: number
    stuck_payments: number
  }
  labels: {
    summary: Record<LabelKind, { charges: number; orders: number; amount: number }>
    by_month: ({ month: string } & Record<LabelKind, number>)[]
    unmatched_total: number
    unmatched_count: number
    unmatched: { order_id: string; kind: LabelKind; amount: number; charged: string | null; source: string }[]
  }
  fees: {
    channels: {
      channel: Channel
      lines: number
      median_rate: number
      recent_rate: number | null
      baseline_rate: number | null
      drift: number | null
      drift_alert: boolean
    }[]
    zero_fee_count: number
    high_fee_count: number
    zero_fee: { channel: Channel; sku: string; order_id: string; date: string; gross: number; fees: number }[]
    high_fee: {
      channel: Channel
      sku: string
      order_id: string
      date: string
      gross: number
      fees: number
      rate: number
      typical: number
    }[]
  }
  settlements: {
    failed_count: number
    failed_total: number
    stuck_count: number
    stuck_total: number
    stuck_days: number
    failed: SettlementItem[]
    stuck: SettlementItem[]
  }
}

export interface ReturnsCostResponse {
  days: number
  default_loss_rate: number
  summary: {
    cases: number
    units: number
    refund: number
    shipping_lost: number
    product_lost: number
    real_cost: number
    seller_fault_units: number
    without_cost: number
  }
  by_sku: {
    sku: string
    product_name: string | null
    returned_units: number
    refund: number
    shipping_lost: number
    product_lost: number
    seller_fault_units: number
    has_cost: boolean
    real_cost: number
    units_sold: number
    return_rate: number | null
    cost_per_return: number
  }[]
  by_reason: {
    reason: string
    cases: number
    units: number
    refund: number
    shipping_lost: number
    product_lost: number
    loss_rate: number
    seller_fault: boolean
    known_reason: boolean
    real_cost: number
  }[]
}

export interface ShippingRow {
  key: string
  orders: number
  units: number
  net_sales: number
  shipping: number
  shipping_share: number | null
  shipping_per_order: number | null
  shipping_per_unit: number | null
  contribution: number
  margin: number | null
  product_name?: string | null
  flag?: boolean
}

export interface ShippingProfitResponse {
  days: number
  threshold: number
  summary: {
    orders: number
    orders_with_state: number
    state_coverage: number | null
    shipping: number
    net_sales: number
    skus_over_threshold: number
  }
  by_state: ShippingRow[]
  by_sku: ShippingRow[]
}

export type PromoVerdict = 'worth_it' | 'not_worth' | 'insufficient'

export interface PromoEffectResponse {
  days: number
  promo_threshold: number
  summary: { skus: number; worth_it: number; not_worth: number; insufficient: number }
  rows: {
    channel: Channel
    sku: string
    product_name: string | null
    promo_days: number
    normal_days: number
    avg_depth: number
    units_per_day_promo: number
    units_per_day_normal: number
    uplift: number | null
    profit_per_day_promo: number
    profit_per_day_normal: number
    profit_delta_per_day: number | null
    verdict: PromoVerdict
  }[]
  buckets: {
    channel: Channel
    bucket: string
    sku_days: number
    units_index: number
    profit_per_sku_day: number
    margin: number | null
  }[]
}

export type CreatorAction = 'repeat' | 'keep' | 'wait' | 'cut'

export interface CreatorDecisionsResponse {
  wait_days: number
  summary: {
    creators: number
    conversion: number | null
    median_days_to_first_sale: number | null
    avoidable_cost: number
  } & Record<CreatorAction, { creators: number; sample_cost: number; net: number }>
  creators: {
    creator: string
    action: CreatorAction
    samples: number
    sample_cost: number
    orders: number
    gmv: number
    commission: number
    profit: number
    net: number
    roi: number | null
    orders_pending: number
    cost_per_order: number | null
    days_to_first_sale: number | null
    days_since_sample: number
    first_sample: string | null
  }[]
  by_content: {
    content_type: string
    orders: number
    units: number
    gmv: number
    commission: number
    commission_rate: number | null
    creators: number
  }[]
}

// ---------------------------------------------------------------------------- pronóstico, metas y resultados

export type GoalMetric = 'gross_sales' | 'net_profit' | 'units'

export interface ForecastResponse {
  params: { horizon_days: number; lookback_days: number; multiplier: number }
  weekday_adjusted: boolean
  totals: Record<GoalMetric, { forecast: number; low: number; high: number; recent_pace: number; change: number | null }>
  series: ({ date: string; forecast: boolean } & Record<GoalMetric, number>)[]
  by_sku: {
    channel: Channel
    sku: string
    product_name: string | null
    daily_units: number
    forecast_units: number
    forecast_sales: number
    forecast_profit: number
    unit_profit: number | null
  }[]
}

export interface GoalProgress {
  id: number
  month: string
  metric: GoalMetric
  channel: Channel | null
  target: number
  actual: number
  projected: number
  attainment: number | null
  projected_attainment: number | null
  days_left: number
  required_per_day: number | null
  status: 'on_track' | 'at_risk' | 'behind' | 'no_target'
}

export interface GoalsResponse {
  month: string
  days_left: number
  goals: GoalProgress[]
}

export interface PnlRow {
  gross_sales: number
  promo: number
  net_sales: number
  platform_fees: number
  shipping_cost: number
  refund_amount: number
  cogs: number
  gross_profit: number
  ad_spend: number
  contribution: number
  expenses: number
  net_profit: number
  margin: number | null
}

export interface PnlResponse {
  start: string
  end: string
  months: {
    month: string
    channels: ({ channel: Channel } & PnlRow)[]
    total: PnlRow
    unallocated_expenses: number
    expenses_by_category: { category: string; amount: number }[]
  }[]
  summary: PnlRow
  note: string
}

export type Recurrence = 'one_time' | 'monthly' | 'annual'

export interface ExpenseRow {
  id: number
  category_id: number
  category: string | null
  description: string
  amount: number
  recurrence: Recurrence
  start_date: string
  end_date: string | null
  channel: Channel | null
  is_active: boolean
}

export interface ExpenseInput {
  category_id: number
  description: string
  amount: number
  start_date: string
  recurrence: Recurrence
  end_date?: string | null
  channel?: Channel | null
}

const get = <T>(path: string, p: Params = {}) => authFetch(`${path}${qs(p)}`).then((r) => parse<T>(r))
const send = <T>(method: 'PUT' | 'POST' | 'DELETE', path: string, body?: unknown) =>
  authFetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => parse<T>(r))

export const analytics = {
  priceFloor: (s: Scope, o: { days: number; targetMargin: number }) =>
    get<PriceFloorResponse>('/api/pricing/floor', {
      days: o.days,
      target_margin: o.targetMargin,
      channel: s.channel,
      account: s.account,
    }),
  parity: (days: number, threshold: number) => get<ParityResponse>('/api/pricing/parity', { days, threshold }),
  tariffs: (extraDuty: number, days: number) => get<TariffResponse>('/api/tariffs', { extra_duty: extraDuty, days }),
  importLanded: (file: File, applyCosts: boolean) => {
    const body = new FormData()
    body.append('file', file)
    return authFetch(`/api/import/landed-costs${qs({ apply_costs: applyCosts ? 'true' : 'false' })}`, {
      method: 'POST',
      body,
    }).then((r) => parse<LandedImportResult>(r))
  },
  reorder: (s: Scope, o: { lead: number; safety: number; cover: number; multiplier: number }) =>
    get<ReorderResponse>('/api/reorder', {
      lead_time_days: o.lead,
      safety_days: o.safety,
      cover_days: o.cover,
      demand_multiplier: o.multiplier,
      channel: s.channel,
      account: s.account,
    }),
  cashProjection: (s: Scope, horizon: number) =>
    get<CashProjectionResponse>('/api/cashflow/projection', {
      horizon_days: horizon,
      channel: s.channel,
      account: s.account,
    }),
  reconciliation: () => get<ReconciliationResponse>('/api/reconciliation'),
  returnsCost: (s: Scope, days: number, lossRate: number) =>
    get<ReturnsCostResponse>('/api/returns/cost', {
      days,
      default_loss_rate: lossRate,
      channel: s.channel,
      account: s.account,
    }),
  shipping: (s: Scope, days: number) =>
    get<ShippingProfitResponse>('/api/shipping-profitability', { days, channel: s.channel, account: s.account }),
  promoEffect: (s: Scope, days: number) =>
    get<PromoEffectResponse>('/api/promotions/effect', { days, channel: s.channel, account: s.account }),
  creators: (s: Scope, start?: string, end?: string) =>
    get<CreatorDecisionsResponse>('/api/creators/decisions', { start, end, channel: s.channel, account: s.account }),
  forecast: (s: Scope, o: { horizon: number; multiplier: number }) =>
    get<ForecastResponse>('/api/forecast', {
      horizon_days: o.horizon,
      multiplier: o.multiplier,
      channel: s.channel,
      account: s.account,
    }),
  goals: (month: string, multiplier: number) => get<GoalsResponse>('/api/goals', { month, multiplier }),
  saveGoal: (g: { month: string; metric: GoalMetric; target: number; channel?: Channel | null }) =>
    send<{ id: number }>('PUT', '/api/goals', g),
  deleteGoal: (id: number) => send<{ deleted: number }>('DELETE', `/api/goals/${id}`),
  pnl: (start: string, end: string, channel?: Channel | '') => get<PnlResponse>('/api/pnl', { start, end, channel }),
  pnlCsv: async (start: string, end: string, channel?: Channel | '') => {
    const res = await authFetch(`/api/pnl.csv${qs({ start, end, channel })}`)
    if (!res.ok) throw new Error(`Error ${res.status}`)
    return res.blob()
  },
  expenses: () => get<ExpenseRow[]>('/api/expenses'),
  expenseCategories: () => get<{ id: number; code: string; name: string }[]>('/api/expense-categories'),
  addExpense: (e: ExpenseInput) => send<{ id: number }>('POST', '/api/expenses', e),
  deleteExpense: (id: number) => send<{ deleted: number }>('DELETE', `/api/expenses/${id}`),
}
