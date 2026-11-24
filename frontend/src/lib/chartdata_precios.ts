// Datos de gráficas de Promociones (backend: app/chartdata_precios.py, prefijo /api/chartdata).
import { authFetch, parse, query, type Channel } from '@/lib/api'

export interface PromoWindow {
  days: number
  units_per_day: number | null
  sales_per_day: number | null
}

export interface PromoPeriod {
  start: string
  end: string
  days: number
  avg_depth: number
  before: PromoWindow
  during: PromoWindow
  after: PromoWindow
  uplift: number | null
}

export interface PromoTimelineResponse {
  days: number
  promo_threshold: number
  series: { date: string; units: number; net_sales: number; depth: number; promo: boolean }[]
  periods: PromoPeriod[]
}

export const chartdata = {
  promoTimeline: (days: number, channel?: Channel | '', account?: string) =>
    authFetch(`/api/chartdata/promo-timeline${query({ days: String(days), channel: channel || undefined, account: account || undefined })}`).then(
      (r) => parse<PromoTimelineResponse>(r),
    ),
}
