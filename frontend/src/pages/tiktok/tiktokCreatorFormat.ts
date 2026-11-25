// Constantes y funciones puras compartidas por Oportunidades y Panorama de TikTok Creator.
import { useEffect, useState } from 'react'

import { pct1 } from '@/components/analytics/ui'
import { count, money } from '@/lib/format'
import type { CreatorOpportunity, OpportunitySort, ScoreConstants } from '@/lib/tiktokCreatorCatalog'

import { NO_VALUE } from './tiktokFormat'

/** La pantalla de Panorama deja aquí la categoría elegida y la de Oportunidades la lee al montar. */
export const CREATOR_CATEGORY_KEY = 'chk.creator.category'
/** Pestaña a la que lleva el clic de una categoría (la define el menú de TikTok). */
export const OPPORTUNITIES_TAB = 'creatorOpportunities'
/** Así llama el backend a los productos sin categoría; no sirve como filtro. */
export const NO_CATEGORY = 'Sin categoría'

export const PAGE_SIZE = 20
export const SEARCH_DEBOUNCE_MS = 400

/** Desde este puntaje una oportunidad se considera fuerte; por debajo de MID, débil. */
export const SCORE_HIGH = 60
export const SCORE_MID = 30

export const SORT_OPTIONS: { value: OpportunitySort; label: string }[] = [
  { value: 'score', label: 'Puntaje' },
  { value: 'commission', label: 'Comisión por unidad' },
  { value: 'units', label: 'Unidades vendidas' },
  { value: 'price', label: 'Precio' },
]

/** Nombre legible de cada grupo de afinidad que define el backend. */
const AFFINITY_GROUP_LABEL: Record<string, string> = { hogar: 'Hogar', cocina: 'Cocina', navidad: 'Navidad', empaque: 'Empaque' }
export const affinityGroupLabel = (group: string) => AFFINITY_GROUP_LABEL[group] ?? group

export type ScoreTier = 'high' | 'mid' | 'low'
export const scoreTier = (score: number): ScoreTier => (score >= SCORE_HIGH ? 'high' : score >= SCORE_MID ? 'mid' : 'low')

/** ¿Vendemos algo parecido? (producto equivalente o misma categoría). */
export const relatesToOurCatalog = (p: CreatorOpportunity) => p.relation.sells_same_category || p.relation.equivalent != null

/** Explica de dónde salió el factor de afinidad según las constantes del backend. */
export function affinityReason(value: number, constants: ScoreConstants): string {
  const { category, title } = constants.affinity
  if (value === category) return 'Una palabra clave de nuestro negocio aparece en la categoría'
  if (value === title) return 'Una palabra clave de nuestro negocio aparece solo en el título'
  return 'No se parece a lo que vendemos (hogar, cocina, navidad o empaque)'
}

/** Solo enlaces web: evita `javascript:` u otros esquemas que pudieran venir en un dato externo. */
export function safeHttpUrl(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.toString() : null
  } catch {
    return null
  }
}

/** Categoría pendiente de Panorama. El almacenamiento puede estar bloqueado: nunca debe romper la pantalla. */
export function peekPendingCategory(): string {
  try {
    return sessionStorage.getItem(CREATOR_CATEGORY_KEY) ?? ''
  } catch {
    return ''
  }
}

export function clearPendingCategory(): void {
  try {
    sessionStorage.removeItem(CREATOR_CATEGORY_KEY)
  } catch {
    /* sin almacenamiento: no hay nada que borrar */
  }
}

export function storePendingCategory(category: string): void {
  try {
    sessionStorage.setItem(CREATOR_CATEGORY_KEY, category)
  } catch {
    /* sin almacenamiento: se abre Oportunidades sin filtro */
  }
}

/** Devuelve `value` tras `delay` ms sin cambios (evita una consulta por tecla). */
export function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(id)
  }, [value, delay])
  return debounced
}

/** Importes de un producto: los sospechosos son datos de prueba, así que no se muestra ninguno. */
export const priceText = (p: CreatorOpportunity) => (p.suspect ? NO_VALUE : money(p.price))
export const unitsText = (p: CreatorOpportunity) => (p.suspect ? NO_VALUE : count(p.units_sold))
export const perUnitText = (p: CreatorOpportunity) => (p.suspect ? NO_VALUE : money(p.commission_per_unit))
export const rateText = (p: CreatorOpportunity) => (p.suspect ? NO_VALUE : pct1(p.commission_rate))
