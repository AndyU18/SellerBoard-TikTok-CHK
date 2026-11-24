// Formatos y hooks de apoyo de las pantallas de TikTok orgánico.
import { count, percent } from '@/lib/format'
import { tiktokOrganicData, type OrganicVideo } from '@/lib/tiktokOrganicData'
import { useAsync } from '@/lib/useAsync'

/** Pestaña de la pantalla de Conexión (la usa `onOpenTab`). */
export const CONNECTION_TAB = 'organicConnection'
const DEFAULT_PLACEHOLDER = 'Video sin título'
const SECONDS_PER_MINUTE = 60
const TRACKING_LOOKBACK_DAYS = 365

export const shortDate = (value: string | number): string => {
  const text = String(value)
  const date = new Date(text.length === 10 ? `${text}T00:00:00` : text)
  return Number.isNaN(date.getTime()) ? text : date.toLocaleDateString('es-US', { day: 'numeric', month: 'short' })
}

export const longDate = (value: string | null): string => {
  if (!value) return '—'
  const date = new Date(value.length === 10 ? `${value}T00:00:00` : value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('es-US', { day: 'numeric', month: 'short', year: 'numeric' })
}

/** 75 -> 1:15 */
export const clock = (seconds: number | null): string =>
  seconds == null ? '—' : `${Math.floor(seconds / SECONDS_PER_MINUTE)}:${String(seconds % SECONDS_PER_MINUTE).padStart(2, '0')}`

export const avgViews = (v: number | null | undefined): string => (v == null ? '—' : count(Math.round(v)))
export const signed = (v: number): string => `${v > 0 ? '+' : ''}${count(v)}`
export const pct = (v: number | null | undefined): string => percent(v)

export const videoTitle = (v: Pick<OrganicVideo, 'title' | 'description' | 'video_id'>): string =>
  v.title?.trim() || v.description?.trim() || `${DEFAULT_PLACEHOLDER} (${v.video_id.slice(-6)})`

/** Primer día con historial. Las pantallas sin la serie propia lo piden aquí (máximo de días, para no recortarlo). */
export function useTrackingSince(): string | null {
  const state = useAsync(() => tiktokOrganicData.performance(TRACKING_LOOKBACK_DAYS), [])
  return state.data?.tracking_since ?? null
}
