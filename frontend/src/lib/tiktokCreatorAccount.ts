// Tipos, llamadas y cálculos puros de la cuenta de creador propia de TikTok (afiliados).
// Backend: app/tiktok_creator_api.py y app/tiktok_creator_analytics.py (prefijo /api/tiktok-creator). Solo lectura.
// Lo importan TikTokCreatorProfile, TikTokCreatorOrders y TikTokCreatorShowcase (pages/tiktok).
import { authFetch, parse, query } from '@/lib/api'

const BASE = '/api/tiktok-creator'
const MS_PER_DAY = 86_400_000

/** Días de margen por debajo de los cuales un vencimiento pasa de verde a ámbar. */
export const EXPIRY_WARN_DAYS = 7

/** Estados de pedido de afiliado cuya comisión ya se pagó. */
export const PAID_ORDER_STATUSES: readonly string[] = ['PAID', 'SETTLED', 'COMPLETED']
/** Estados de pedido de afiliado cuya comisión no se cobrará. */
export const VOID_ORDER_STATUSES: readonly string[] = ['CANCELLED', 'CANCELED', 'REFUNDED', 'RETURNED']

export interface CreatorTokens {
  access_expires_at: string | null
  refresh_expires_at: string | null
  access_valid: boolean
  refresh_valid: boolean
}

export interface CreatorLastRun {
  status: string
  mode: string | null
  started_at: string | null
  error: string | null
}

export interface CreatorCoverage {
  loaded: number
  /** Total que declara TikTok; null si aún no se conoce. */
  total: number | null
  /** Fracción 0-1 cargado/total; null si no hay total. */
  pct: number | null
  visible_cap: number
  note: string
}

export interface CreatorStatus {
  configured: boolean
  connected: boolean
  needs_reauth: boolean
  message: string
  tokens: CreatorTokens | null
  last_run: CreatorLastRun | null
  last_light_sync_at: string | null
  last_full_sync_at: string | null
  next_full_due_at: string | null
  coverage: CreatorCoverage
}

export interface CreatorPermission {
  code: string
  label: string
  explanation: string
  granted: boolean
}

export interface CreatorProfileData {
  username: string | null
  creator_user_id: string | null
  avatar_url: string | null
  user_type: string | null
  seller_type: string | null
  register_region: string | null
  selection_region: string | null
  snapshot_date: string | null
  catalog_total: number | null
  showcase_total: number | null
}

export interface CreatorProfileResponse {
  available: boolean
  profile: CreatorProfileData | null
  permissions: CreatorPermission[]
  permissions_note: string
}

export interface CreatorOrder {
  order_id: string
  status: string | null
  created_at: string | null
  commission_amount: number | null
  currency: string | null
}

export interface CreatorOrdersResponse {
  days: number
  available: boolean
  summary: { orders: number; commission: number }
  orders: CreatorOrder[]
}

export interface ShowcaseProduct {
  product_id: string
  origin: string | null
  title: string | null
  image_url: string | null
  original_price_min: number | null
  original_price_max: number | null
  discount_price_min: number | null
  discount_price_max: number | null
  currency: string | null
  review_status: string | null
  inventory_status: string | null
  added_status: string | null
  is_hidden: boolean
  shop_name: string | null
}

export interface ShowcaseResponse {
  available: boolean
  summary: {
    products: number
    hidden: number
    by_review_status: Record<string, number>
    by_inventory_status: Record<string, number>
  }
  products: ShowcaseProduct[]
}

export const tiktokCreatorAccount = {
  status: () => authFetch(`${BASE}/status`).then((r) => parse<CreatorStatus>(r)),
  profile: () => authFetch(`${BASE}/profile`).then((r) => parse<CreatorProfileResponse>(r)),
  orders: (days: number) => authFetch(`${BASE}/orders${query({ days: String(days) })}`).then((r) => parse<CreatorOrdersResponse>(r)),
  showcase: () => authFetch(`${BASE}/showcase`).then((r) => parse<ShowcaseResponse>(r)),
}

export type ExpiryTone = 'ok' | 'soon' | 'expired' | 'unknown'

export interface Expiry {
  /** Días enteros que faltan (negativo si ya venció); null si no hay fecha. */
  daysLeft: number | null
  tone: ExpiryTone
}

/** Días restantes hasta un vencimiento y su semáforo (verde, ámbar al acercarse, rojo vencido). */
export function expiryOf(iso: string | null | undefined, now: Date = new Date()): Expiry {
  if (!iso) return { daysLeft: null, tone: 'unknown' }
  const time = new Date(iso).getTime()
  if (Number.isNaN(time)) return { daysLeft: null, tone: 'unknown' }
  const daysLeft = Math.floor((time - now.getTime()) / MS_PER_DAY)
  if (time <= now.getTime()) return { daysLeft, tone: 'expired' }
  return { daysLeft, tone: daysLeft < EXPIRY_WARN_DAYS ? 'soon' : 'ok' }
}

export interface CommissionTotals {
  paid: number
  pending: number
}

const isIn = (list: readonly string[], status: string | null) => list.includes((status ?? '').toUpperCase())

/** Reparte la comisión entre pagada y pendiente; los pedidos cancelados o devueltos no cuentan en ninguna. */
export function commissionTotals(orders: readonly CreatorOrder[]): CommissionTotals {
  return orders.reduce<CommissionTotals>(
    (acc, o) => {
      const amount = o.commission_amount ?? 0
      if (isIn(VOID_ORDER_STATUSES, o.status)) return acc
      if (isIn(PAID_ORDER_STATUSES, o.status)) return { ...acc, paid: acc.paid + amount }
      return { ...acc, pending: acc.pending + amount }
    },
    { paid: 0, pending: 0 },
  )
}

export type DailyCommission = {
  day: string
  orders: number
  commission: number
}

/** Pedidos y comisión por día (YYYY-MM-DD, orden cronológico) para la gráfica diaria. */
export function commissionByDay(orders: readonly CreatorOrder[]): DailyCommission[] {
  const days = new Map<string, DailyCommission>()
  for (const o of orders) {
    if (!o.created_at) continue
    const day = o.created_at.slice(0, 10)
    const cur = days.get(day) ?? { day, orders: 0, commission: 0 }
    days.set(day, { day, orders: cur.orders + 1, commission: cur.commission + (o.commission_amount ?? 0) })
  }
  return [...days.values()].sort((a, b) => a.day.localeCompare(b.day))
}
