// Tipos y llamadas de Promociones, Cupones y Paquetes de TikTok Shop.
// Backend: app/tiktok_extra_api.py + app/tiktok_extra_analytics.py (solo lectura, prefijo /api/tiktok).
// Importado por pages/tiktok/TikTokPromotions, TikTokCoupons y TikTokPackages.
import { authFetch, parse, query } from '@/lib/api'

const BASE = '/api/tiktok'

// --- promociones -------------------------------------------------------------------------------------

/** Unidades por día durante la actividad contra los días previos (comparación observacional). */
export interface PromoRoi {
  days_running: number
  units_per_day_during: number
  units_per_day_before: number
  /** (durante - antes) / antes; null si antes no hubo ventas. */
  uplift: number | null
  units_during: number
  sales_during: number
  sales_per_day_during: number
  sales_per_day_before: number
}

export interface PromoProduct {
  product_id: string
  title: string | null
  activity_price: number | null
  discount: string | null
  quantity_limit: number | null
}

/** `shop` = actividad de nivel tienda (sin productos): se mide contra todas las ventas de TikTok. */
export type PromoRoiScope = 'shop' | 'products'

export interface PromoActivity {
  activity_id: string
  title: string | null
  type: string | null
  level: string | null
  status: string | null
  begin_at: string | null
  end_at: string | null
  product_count: number | null
  discount: string | null
  products: PromoProduct[]
  roi: PromoRoi | null
  roi_scope: PromoRoiScope
}

export interface PromotionsReport {
  available: boolean
  summary: {
    activities: number
    by_status: Record<string, number>
    by_type: Record<string, number>
    baseline_days: number
  }
  activities: PromoActivity[]
}

// --- cupones -----------------------------------------------------------------------------------------

export interface Coupon {
  coupon_id: string
  title: string | null
  status: string | null
  display_type: string | null
  promo_code: string | null
  scope: string | null
  segment: string | null
  discount_type: string | null
  discount: string
  min_spend: number | null
  valid_from: string | null
  valid_until: string | null
  claimed: number | null
  redeemed: number | null
  /** canjeados / reclamados; null si nadie lo reclamó. */
  redemption_rate: number | null
  redemption_limit: number | null
  product_count: number
}

export interface CouponsReport {
  available: boolean
  summary: {
    coupons: number
    by_status: Record<string, number>
    by_discount_type: Record<string, number>
    redeemed: number
    /** Total que TikTok dice tener, si el backend lo informa (el listado puede traer menos). */
    reported_total?: number | null
  }
  coupons: Coupon[]
}

// --- paquetes ----------------------------------------------------------------------------------------

export interface StuckPackage {
  package_id: string
  status: string | null
  tracking_number: string | null
  order_count: number | null
  created_at: string | null
  last_movement: string | null
  last_event: string | null
  days_without_movement: number
}

/** Una fila por día de creación: conteo por estado (claves dinámicas) y `total`. */
export type PackageDay = { date: string; total: number } & Record<string, string | number>

export interface PackagesReport {
  available: boolean
  summary: {
    packages: number
    by_status: Record<string, number>
    delivery_rate: number | null
    avg_delivery_days: number | null
    stuck: number
    stuck_days: number
    delivered_at_note: string
  }
  delivery_distribution: { bucket: string; count: number }[]
  by_day: PackageDay[]
  stuck: StuckPackage[]
}

export const tiktokPromos = {
  promotions: (days: number) => authFetch(`${BASE}/promotions${query({ days: String(days) })}`).then((r) => parse<PromotionsReport>(r)),
  coupons: (days: number) => authFetch(`${BASE}/coupons${query({ days: String(days) })}`).then((r) => parse<CouponsReport>(r)),
  packages: (days: number, stuckDays: number) =>
    authFetch(`${BASE}/packages${query({ days: String(days), stuck_days: String(stuckDays) })}`).then((r) => parse<PackagesReport>(r)),
}
