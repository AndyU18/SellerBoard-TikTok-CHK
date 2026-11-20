import { supabase } from '@/lib/supabase'

export type Channel = 'amazon' | 'tiktok_shop' | 'walmart'
export type GroupBy = 'channel' | 'product' | 'channel_product' | 'account'

export const CHANNELS: Channel[] = ['amazon', 'tiktok_shop', 'walmart']
export const CHANNEL_LABEL: Record<Channel, string> = {
  amazon: 'Amazon',
  tiktok_shop: 'TikTok Shop',
  walmart: 'Walmart',
}
export const CHANNEL_COLOR: Record<Channel, string> = {
  amazon: '#ff9900',
  tiktok_shop: '#fe2c55',
  walmart: '#0071ce',
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
}

export interface Filters {
  start?: string
  end?: string
  channel?: Channel | ''
}

export interface ImportResult {
  inserted: number
  updated: number
  accounts_created: number
  products_created: number
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
    authFetch(`/api/profit${query({ group_by: groupBy, start: f.start, end: f.end, channel: f.channel })}`).then(
      (r) => parse<ProfitResponse>(r),
    ),
  timeseries: (interval: 'day' | 'month', f: Filters) =>
    authFetch(
      `/api/profit/timeseries${query({ interval, start: f.start, end: f.end, channel: f.channel })}`,
    ).then((r) => parse<{ interval: string; rows: ProfitRow[] }>(r)),
  products: () => authFetch('/api/products').then((r) => parse<Product[]>(r)),
  updateCogs: (id: number, cogs_unit: number) =>
    authFetch(`/api/products/${id}/cogs`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cogs_unit }),
    }).then((r) => parse<{ id: number; cogs_unit: number }>(r)),
  upload: (kind: 'orders' | 'ad-spend' | 'cogs', file: File) => {
    const body = new FormData()
    body.append('file', file)
    return authFetch(`/api/import/${kind}`, { method: 'POST', body }).then((r) => parse<ImportResult>(r))
  },
  syncTikTok: (days: number) =>
    authFetch(`/api/sync/tiktok-shop?days=${days}`, { method: 'POST' }).then((r) =>
      parse<{ days: number; stats: Record<string, number>; imported: ImportResult }>(r),
    ),
}
