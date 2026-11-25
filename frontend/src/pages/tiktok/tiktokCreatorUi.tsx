// Piezas visuales compartidas por Oportunidades y Panorama de TikTok Creator.
import { Callout, Pill, PILL, pct1 } from '@/components/analytics/ui'
import { count } from '@/lib/format'
import type { CatalogCoverage, CreatorOpportunity, CreatorStatus } from '@/lib/tiktokCreatorCatalog'

import { type ScoreTier, scoreTier } from './tiktokCreatorFormat'

const SCORE_BAR_CLASS: Record<ScoreTier, string> = { high: 'bg-emerald-500', mid: 'bg-amber-500', low: 'bg-slate-400' }
const SCORE_MAX = 100

/** Barra de puntaje 0-100 con su valor; accesible como medidor. */
export function ScoreBar({ score, className = '' }: { score: number; className?: string }) {
  const width = Math.max(0, Math.min(SCORE_MAX, score))
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div role="meter" aria-label="Puntaje de oportunidad" aria-valuemin={0} aria-valuemax={SCORE_MAX} aria-valuenow={score} className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200">
        <div className={`h-full rounded-full ${SCORE_BAR_CLASS[scoreTier(score)]}`} style={{ width: `${width}%` }} />
      </div>
      <span className="w-9 text-right text-xs font-semibold tabular-nums">{score.toFixed(0)}</span>
    </div>
  )
}

/** Insignias de un producto: nuevo, ya vendemos algo así, misma categoría y datos sospechosos. */
export function OpportunityBadges({ p }: { p: CreatorOpportunity }) {
  const { equivalent, sells_same_category: sameCategory } = p.relation
  return (
    <div className="flex flex-wrap gap-1">
      {p.suspect && <Pill label="Datos sospechosos" className={PILL.red} />}
      {p.is_new && <Pill label="Nuevo" className={PILL.blue} />}
      {equivalent ? (
        <Pill label="Ya vendemos algo así" className={PILL.green} />
      ) : (
        sameCategory && <Pill label="Misma categoría que lo nuestro" className={PILL.slate} />
      )}
      {!p.has_inventory && <Pill label="Sin inventario" className={PILL.amber} />}
    </div>
  )
}

function coverageMessage(c: CatalogCoverage): string {
  const share = c.pct != null ? ` (${pct1(c.pct)})` : ''
  return `Solo están cargados ${count(c.loaded)} de ${count(c.total)} productos${share}. TikTok entrega 20 por consulta y deja ver como máximo ${count(c.visible_cap)}; los resultados describen solo lo cargado.`
}

/** Aviso cuando el catálogo cargado es parcial o la cuenta no está conectada. Sin aviso si todo está completo. */
export function CoverageNotice({ status }: { status: CreatorStatus | null }) {
  if (!status) return null
  const { coverage } = status
  const partial = coverage.total != null && coverage.loaded < coverage.total
  return (
    <>
      {!status.connected && <Callout tone="warning">{status.message}</Callout>}
      {partial && <Callout tone="warning">{coverageMessage(coverage)}</Callout>}
    </>
  )
}

/** Paginación anterior / siguiente con la posición actual. */
export function Pager({ page, pages, onChange }: { page: number; pages: number; onChange: (page: number) => void }) {
  const button = 'rounded-md border px-3 py-1.5 text-sm hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none disabled:opacity-40'
  return (
    <nav aria-label="Paginación" className="flex items-center justify-center gap-3 pt-4">
      <button type="button" className={button} disabled={page <= 0} onClick={() => onChange(page - 1)}>
        Anterior
      </button>
      <span className="text-muted-foreground text-sm" aria-live="polite">
        Página {count(page + 1)} de {count(Math.max(pages, 1))}
      </span>
      <button type="button" className={button} disabled={page + 1 >= pages} onClick={() => onChange(page + 1)}>
        Siguiente
      </button>
    </nav>
  )
}
