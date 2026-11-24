// Formato y filtrado puros de las pantallas de producto, SKU y catálogo de TikTok Shop.
import { NO_VALUE } from './tiktokFormat'

const MAX_INITIALS = 2
export const MARGIN_GOOD = 0.15
const MARGIN_OK = 0.05

/** Iniciales de un título para el marcador cuando no hay imagen ("Taza azul grande" -> "TA"). */
export function initials(title: string | null | undefined): string {
  const letters = (title ?? '')
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0])
    .join('')
    .slice(0, MAX_INITIALS)
    .toUpperCase()
  return letters || NO_VALUE
}

/** Color de texto según margen: verde sano, ámbar justo, rojo negativo o bajo. */
export function marginClass(margin: number | null | undefined): string {
  if (margin == null) return ''
  if (margin >= MARGIN_GOOD) return 'text-emerald-600'
  if (margin >= MARGIN_OK) return 'text-amber-600'
  return 'text-red-600'
}

/** Coincidencia sin mayúsculas ni espacios sobrantes; sin búsqueda deja pasar todo. */
export function matchesSearch(search: string, ...fields: (string | null | undefined)[]): boolean {
  const needle = search.trim().toLowerCase()
  return !needle || fields.some((f) => f?.toLowerCase().includes(needle))
}

/** Recorta un título largo para etiquetas de gráficas. */
export function clip(text: string | null | undefined, max: number): string {
  const t = text ?? NO_VALUE
  return t.length > max ? `${t.slice(0, max - 1)}…` : t
}
