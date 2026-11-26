// Piezas visuales compartidas por las pantallas del mercado de creadores de TikTok Shop.
import { useState } from 'react'

import { Callout, Hint, PILL, Pill } from '@/components/analytics/ui'
import { count } from '@/lib/format'
import type { MarketCreator, MarketStatus, ScorePart } from '@/lib/tiktokCreatorMarket'

import { initials } from './tiktokCatalogFormat'
import {
  ENGAGEMENT_NOTE,
  RANGE_NOTE,
  SCORE_MAX,
  SCORE_TIER_LABEL,
  SELLER_CENTER_LABEL,
  SELLER_CENTER_URL,
  displayName,
  isNoSamples,
  isWorkingWith,
  partLabel,
  scoreTier,
  type ScoreTier,
} from './tiktokMarketFormat'
import { shortDateTime } from './tiktokFormat'

const SCORE_BAR_CLASS: Record<ScoreTier, string> = { high: 'bg-emerald-500', mid: 'bg-amber-500', low: 'bg-slate-400' }
const PART_BAR_CLASS = 'bg-sky-500'

/** Avatar público del creador con carga diferida; si falta o falla muestra las iniciales. */
export function CreatorAvatar({ c, className = 'size-12' }: { c: MarketCreator; className?: string }) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null)
  const name = displayName(c)
  const src = c.avatar_url
  if (!src || failedSrc === src) {
    return (
      <span role="img" aria-label={`Sin avatar: ${name}`} className={`${className} flex shrink-0 items-center justify-center rounded-full bg-slate-100 text-sm font-semibold text-slate-500`}>
        {initials(name)}
      </span>
    )
  }
  return <img src={src} alt={name} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailedSrc(src)} className={`${className} shrink-0 rounded-full bg-slate-100 object-cover`} />
}

/** Barra de puntaje de candidato 0-100 con su valor; accesible como medidor. */
export function MarketScoreBar({ score, className = '' }: { score: number; className?: string }) {
  const width = Math.max(0, Math.min(SCORE_MAX, score))
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div
        role="meter"
        aria-label="Puntaje de candidato"
        aria-valuemin={0}
        aria-valuemax={SCORE_MAX}
        aria-valuenow={score}
        aria-valuetext={`${score.toFixed(0)} de ${SCORE_MAX}: ${SCORE_TIER_LABEL[scoreTier(score)]}`}
        className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200"
      >
        <div className={`h-full rounded-full ${SCORE_BAR_CLASS[scoreTier(score)]}`} style={{ width: `${width}%` }} />
      </div>
      <span className="w-9 text-right text-xs font-semibold tabular-nums">{score.toFixed(0)}</span>
    </div>
  )
}

/** Desglose del puntaje: una barra fina por parte (ajuste, rendimiento, tamaño) con sus puntos. */
export function PartBars({ parts, className = '' }: { parts: ScorePart[]; className?: string }) {
  return (
    <div role="list" className={`block space-y-1 ${className}`} aria-label="Desglose del puntaje">
      {parts.map((p) => (
        <div role="listitem" key={p.name} className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground w-20 shrink-0">{partLabel(p.name)}</span>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200" aria-hidden>
            <span className={`block h-full rounded-full ${PART_BAR_CLASS}`} style={{ width: `${p.max > 0 ? (p.points / p.max) * 100 : 0}%` }} />
          </span>
          <span className="w-12 text-right tabular-nums">
            {p.points.toFixed(0)}/{p.max.toFixed(0)}
          </span>
        </div>
      ))}
    </div>
  )
}

/** Insignias del creador: candidato, no enviar muestras y ya trabajamos con él. */
export function MarketBadges({ c }: { c: MarketCreator }) {
  return (
    <div className="flex flex-wrap gap-1">
      {c.is_candidate && <Pill label="Candidato" className={PILL.green} />}
      {isNoSamples(c) && <Pill label="No enviar muestras" className={PILL.red} />}
      {isWorkingWith(c) && <Pill label="Ya trabajamos con él" className={PILL.blue} />}
      {c.is_new && <Pill label="Nuevo" className={PILL.slate} />}
    </div>
  )
}

/** Enlace de texto al Seller Center: la acción (invitar, muestra, colaboración) se hace allá, no aquí. */
export function SellerCenterLink() {
  return (
    <a href={SELLER_CENTER_URL} target="_blank" rel="noopener noreferrer" className="inline-block rounded-md border px-3 py-2 text-sm font-medium hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
      {SELLER_CENTER_LABEL}
    </a>
  )
}

/** Cobertura parcial y fecha de la última sincronización del mercado. */
export function MarketCoverageNotice({ status }: { status: MarketStatus | null }) {
  if (!status) return null
  const { coverage, last_sync: sync } = status
  const when = sync ? shortDateTime(sync.finished_at ?? sync.started_at) : null
  return (
    <>
      <Callout tone="warning">
        Cobertura parcial: TikTok entrega {count(status.limits.creator_page_size)} creadores por consulta, así que solo están cargados {count(coverage.creators)} ({count(coverage.creators_active)} activos). Los resultados describen solo lo cargado.
        {when ? ` Última sincronización: ${when}.` : ' Todavía no hay una sincronización registrada.'}
      </Callout>
      {sync?.error && <Callout tone="danger">La última sincronización terminó con un error: {sync.error}</Callout>}
    </>
  )
}

/** Pie de las tablas y fichas: engagement con asterisco y GMV como rango. */
export function MarketFootnotes() {
  return (
    <div className="space-y-1">
      <Hint>{ENGAGEMENT_NOTE}</Hint>
      <Hint>{RANGE_NOTE}</Hint>
      <Hint>Son datos públicos de creadores (usuario, seguidores, categorías). Esta pantalla es solo de lectura: invitar, enviar muestras o crear colaboraciones se hace en el Seller Center de TikTok Shop.</Hint>
    </div>
  )
}
