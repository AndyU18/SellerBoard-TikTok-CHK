// Datos de gráficas de dinero (backend: app/chartdata_dinero.py, prefijo /api/chartdata).
import { authFetch, parse, query, type Filters } from '@/lib/api'

export interface LiveSessionPoint {
  external_id: string
  username: string | null
  start_at: string
  gmv: number
  orders: number
  customers: number
  units: number
  duration_min: number | null
}

export interface CreativeWeeklyResponse {
  weeks: string[]
  rows: {
    content_type: string
    content_id: string
    creator: string | null
    gmv: number
    weeks: Record<string, { gmv: number; orders: number }>
  }[]
}

export interface FunnelDeliveryResponse {
  orders: number
  shipped: number
  delivered: number
}

const q = (f: Filters, extra: Record<string, string | undefined> = {}) =>
  query({ start: f.start, end: f.end, channel: f.channel || undefined, account: f.account || undefined, ...extra })

export const chartdataDinero = {
  liveSessions: (f: Filters) =>
    authFetch(`/api/chartdata/live-sessions${q(f)}`).then((r) => parse<{ sessions: LiveSessionPoint[]; timezone: string }>(r)),
  creativeWeekly: (f: Filters, limit = 15) =>
    authFetch(`/api/chartdata/creative-weekly${q(f, { limit: String(limit) })}`).then((r) => parse<CreativeWeeklyResponse>(r)),
  funnelDelivery: (f: Filters) =>
    authFetch(`/api/chartdata/funnel-delivery${q(f)}`).then((r) => parse<FunnelDeliveryResponse>(r)),
}

/** Lunes de la semana de una fecha ISO (AAAA-MM-DD), como clave de agrupación. */
export function weekKey(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7))
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** "2026-11-09" -> "09/11" para ejes. */
export const shortDate = (iso: string | number) => {
  const s = String(iso)
  return `${s.slice(8, 10)}/${s.slice(5, 7)}`
}
