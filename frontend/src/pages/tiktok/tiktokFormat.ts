// Formatos compartidos por las pantallas de TikTok Shop (promociones, cupones, paquetes).
export const NO_VALUE = '—'

const DATE_LOCALE = 'es-US'

/** `2026-11-24T10:00:00Z` -> `24 nov 2026`. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return NO_VALUE
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? NO_VALUE : d.toLocaleDateString(DATE_LOCALE, { day: 'numeric', month: 'short', year: 'numeric' })
}

export function shortDateTime(iso: string | null | undefined): string {
  if (!iso) return NO_VALUE
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? NO_VALUE : d.toLocaleString(DATE_LOCALE, { dateStyle: 'medium', timeStyle: 'short' })
}

/** Vigencia en una línea: `1 nov 2026 → 30 nov 2026`. */
export const dateRange = (from: string | null | undefined, to: string | null | undefined) => `${shortDate(from)} → ${shortDate(to)}`

/** Fecha ISO a milisegundos; null si falta o no se entiende. */
export function toMs(iso: string | null | undefined): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  return Number.isNaN(t) ? null : t
}

/** Toma el primer porcentaje de un texto ("20% off", "Descuento 15 %") como fracción (0.2); null si no hay. */
export function parsePercent(text: string | null | undefined): number | null {
  const match = text?.match(/(\d+(?:[.,]\d+)?)\s*%/)
  return match ? Number(match[1].replace(',', '.')) / 100 : null
}

/** Texto legible de un código de TikTok (`PERCENT_OFF` -> `Percent off`) cuando no hay traducción. */
export function humanize(code: string | null | undefined): string {
  if (!code) return NO_VALUE
  const text = code.toLowerCase().replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** Promedio simple; null si no hay valores. */
export function average(values: number[]): number | null {
  return values.length ? values.reduce((a, v) => a + v, 0) / values.length : null
}
