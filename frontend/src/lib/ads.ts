// Tipos y llamadas de Publicidad / TikTok Ads (backend: app/ads_api.py). Importado por pages/Ads.
import { authFetch, parse } from '@/lib/api'

export interface AdsKpis {
  spend: number
  sales: number
  roas: number | null
  tacos: number | null
  spend_per_order: number | null
  orders: number
  profit_before_ads: number
  profit_after_ads: number
}

export type AdsDay = {
  date: string
  spend: number
  sales: number
  orders: number
  roas: number | null
  tacos: number | null
}

export type AdsMonth = {
  month: string
  spend: number
  sales: number
  orders: number
  roas: number | null
  tacos: number | null
}

export interface AdsOverview {
  window: { start: string; end: string; days: number }
  has_spend: boolean
  last_sync: string | null
  kpis: AdsKpis
  daily: AdsDay[]
  monthly: AdsMonth[]
  by_account: { account: string; spend: number }[]
  /** Gasto por día de la semana, lunes = 0. */
  by_weekday: number[]
}

export const ads = {
  overview: (days: number) => authFetch(`/api/ads/overview?days=${days}`).then((r) => parse<AdsOverview>(r)),
  /** POST /api/sync/tiktok-ads (ya existe): trae el gasto diario de TikTok Ads. */
  sync: (days = 90) =>
    authFetch(`/api/sync/tiktok-ads?days=${Math.min(days, 180)}`, { method: 'POST' }).then((r) => parse<unknown>(r)),
}
