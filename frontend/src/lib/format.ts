const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
const int = new Intl.NumberFormat('en-US')

export const money = (v: number | null | undefined) => (v == null ? '—' : usd.format(v))
export const count = (v: number | null | undefined) => (v == null ? '—' : int.format(v))
export const percent = (v: number | null | undefined) =>
  v == null ? '—' : `${(v * 100).toFixed(2)}%`

/** Niega un costo para mostrarlo como gasto, evitando el cero negativo (-$0.00). */
export const neg = (v: number) => (v === 0 ? 0 : -v)

/** Clase de color: rojo si es negativo. */
export const tone = (v: number | null | undefined) =>
  v != null && v < 0 ? 'text-red-600 dark:text-red-400' : ''

export function isoDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export type Preset = '30d' | '90d' | 'ytd' | 'all'
export const PRESETS: { value: Preset; label: string }[] = [
  { value: '30d', label: 'Últimos 30 días' },
  { value: '90d', label: 'Últimos 90 días' },
  { value: 'ytd', label: 'Este año' },
  { value: 'all', label: 'Todo' },
]

export function presetRange(p: Preset): { start?: string; end?: string } {
  const today = new Date()
  const back = (days: number) => {
    const d = new Date(today)
    d.setDate(d.getDate() - days)
    return isoDate(d)
  }
  switch (p) {
    case '30d':
      return { start: back(30), end: isoDate(today) }
    case '90d':
      return { start: back(90), end: isoDate(today) }
    case 'ytd':
      return { start: `${today.getFullYear()}-01-01`, end: isoDate(today) }
    default:
      return {}
  }
}
