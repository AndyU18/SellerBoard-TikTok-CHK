// Constantes y funciones puras compartidas por las pantallas del mercado de creadores de TikTok Shop.
import { pct1 } from '@/components/analytics/ui'
import { count, money } from '@/lib/format'
import type { CandidateCreator, CollabKind, Collaboration, CreatorRelation, MarketCreator, MarketSort, MarketStatus, ScorePart } from '@/lib/tiktokCreatorMarket'

import { NO_VALUE, humanize } from './tiktokFormat'

/** La acción (invitar, enviar muestra, crear colaboración) se hace allá: esta app es solo de lectura. */
export const SELLER_CENTER_URL = 'https://seller-us.tiktok.com/'
export const SELLER_CENTER_LABEL = 'Abrir el Seller Center de TikTok Shop (se abre en otra pestaña)'

/** Pestañas del espacio TikTok (las define tiktokMenu). */
export const TAB_CREATORS = 'marketCreators'
export const TAB_CANDIDATES = 'marketCandidates'
export const TAB_COLLABORATIONS = 'marketCollaborations'
export const TAB_SAMPLE_POLICY = 'sampleStrategy'

export const ENGAGEMENT_NOTE = '* Engagement en puntos base asumidos (1000 = 10 %): unidad no documentada por TikTok.'
export const RANGE_NOTE = 'Solo ~1/3 de los creadores trae GMV exacto: el resto trae un rango de TikTok, que se muestra como rango y nunca como cifra.'

/** Desde este puntaje el backend marca "Candidato"; desde WATCH, "Vigilar". Deben coincidir con tiktok_creator_market_analytics. */
export const CANDIDATE_SCORE = 55
export const WATCH_SCORE = 30
export const SCORE_MAX = 100

/** Pesos del puntaje (suman 100), con lo que mide cada parte. */
export const SCORE_WEIGHTS: { name: string; label: string; weight: number; what: string }[] = [
  { name: 'ajuste', label: 'Ajuste', weight: 35, what: 'Si TikTok lo clasifica en hogar o cocina, o su nombre sugiere decoración navideña, empaque, hogar o cocina.' },
  { name: 'rendimiento', label: 'Rendimiento', weight: 35, what: 'GMV y unidades que TikTok reporta (piso del rango si no hay cifra exacta) y, si se leyó su ficha, el engagement. Escala logarítmica.' },
  { name: 'tamaño', label: 'Tamaño', weight: 30, what: 'Los medianos (10 mil a 100 mil seguidores) rinden más por muestra; los muy pequeños venden poco y los muy grandes rara vez aceptan una muestra.' },
]
const PART_LABEL = Object.fromEntries(SCORE_WEIGHTS.map((w) => [w.name, w.label]))
export const partLabel = (name: string) => PART_LABEL[name] ?? humanize(name)

/** Tramos de tamaño por seguidores (desde, nombre del backend, etiqueta). Espejo de SIZE_TIERS del backend. */
export const SIZE_TIERS: { from: number; tier: string; label: string }[] = [
  { from: 0, tier: 'nano', label: 'Nano (menos de 1 mil)' },
  { from: 1_000, tier: 'micro', label: 'Micro (1 mil a 10 mil)' },
  { from: 10_000, tier: 'mediano', label: 'Medio (10 mil a 100 mil)' },
  { from: 100_000, tier: 'grande', label: 'Grande (100 mil a 500 mil)' },
  { from: 500_000, tier: 'macro', label: 'Macro (500 mil a 1 millón)' },
  { from: 1_000_000, tier: 'mega', label: 'Mega (1 millón o más)' },
]
const SIZE_LABEL = Object.fromEntries(SIZE_TIERS.map((t) => [t.tier, t.label]))
export const sizeLabel = (tier: string) => SIZE_LABEL[tier] ?? humanize(tier)

export const SORT_OPTIONS: { value: MarketSort; label: string }[] = [
  { value: 'score', label: 'Puntaje' },
  { value: 'followers', label: 'Seguidores' },
  { value: 'gmv', label: 'GMV' },
]

/** Relaciones en que no se deben enviar muestras. */
const NO_SAMPLE_RELATIONS: readonly CreatorRelation[] = ['sampled_no_sales', 'paused_by_policy']
export const isNoSamples = (c: MarketCreator) => NO_SAMPLE_RELATIONS.includes(c.relation)
export const isWorkingWith = (c: MarketCreator) => c.relation === 'already_working'

export type ScoreTier = 'high' | 'mid' | 'low'
export const scoreTier = (score: number): ScoreTier => (score >= CANDIDATE_SCORE ? 'high' : score >= WATCH_SCORE ? 'mid' : 'low')
export const SCORE_TIER_LABEL: Record<ScoreTier, string> = { high: 'Candidato', mid: 'Vigilar', low: 'Bajo ajuste' }

export const displayName = (c: Pick<MarketCreator, 'username' | 'nickname' | 'creator_open_id'>) => c.nickname || c.username || c.creator_open_id
export const handleText = (c: Pick<MarketCreator, 'username'>) => (c.username ? `@${c.username}` : NO_VALUE)

/** Rango de TikTok ("$1K-$5K") con raya larga; nunca una cifra exacta. */
export const rangeText = (range: string): string => range.replace(/\s*-\s*/g, '–')

/** GMV: si TikTok solo da rango, el rango; si no, el importe; sin datos, raya. */
export function gmvText(c: MarketCreator): string {
  if (c.gmv_range) return rangeText(c.gmv_range)
  return c.gmv != null ? money(c.gmv) : NO_VALUE
}

export const unitsRangeText = (c: MarketCreator) => (c.units_sold_range ? rangeText(c.units_sold_range) : NO_VALUE)
/** Engagement con asterisco: la unidad no está documentada. */
export const engagementText = (c: MarketCreator) => (c.engagement_rate == null ? NO_VALUE : `${pct1(c.engagement_rate)}*`)
export const categoriesText = (c: MarketCreator) => (c.categories.length > 0 ? c.categories.join(', ') : NO_VALUE)
export const followersText = (c: MarketCreator) => count(c.followers)

/** Parte de los creadores cargados que traen GMV exacto (el resto trae rango); null sin creadores. */
export const exactGmvShare = (status: MarketStatus | null): number | null => {
  if (!status || status.coverage.creators <= 0) return null
  return status.coverage.creators_with_gmv / status.coverage.creators
}

/** Las dos partes que más puntos aportan, de mayor a menor. */
export const topParts = (parts: ScorePart[], n = 2): ScorePart[] => [...parts].sort((a, b) => b.points - a.points).slice(0, n)

/** Una línea por parte con sus puntos y la razón en lenguaje claro. */
export const partLine = (p: ScorePart) => `${partLabel(p.name)} (${p.points.toFixed(0)} de ${p.max.toFixed(0)}): ${p.reason}`

/** Razón en lenguaje claro de por qué un creador está en la shortlist. */
export const candidateReason = (c: CandidateCreator): string => {
  const [first, second] = topParts(c.score_parts)
  const lead = `Puntaje ${c.score.toFixed(0)}: lo que más suma es ${partLabel(first.name).toLowerCase()} (${first.reason})`
  return second ? `${lead} y ${partLabel(second.name).toLowerCase()} (${second.reason}).` : `${lead}.`
}

/** Estados de colaboración que devuelve TikTok. */
const COLLAB_STATUS_LABEL: Record<string, string> = { NORMAL: 'Abierta', ONGOING: 'En curso' }
export const collabStatusLabel = (status: string | null) => (status ? (COLLAB_STATUS_LABEL[status.toUpperCase()] ?? humanize(status)) : NO_VALUE)
export const COLLAB_KIND_LABEL: Record<Exclude<CollabKind, 'all'>, string> = { open: 'Abiertas', target: 'Dirigidas' }

/** Cuántos creadores participan en una colaboración (invitados si es dirigida). */
export const collabCreators = (c: Collaboration) => c.creators ?? c.invited
