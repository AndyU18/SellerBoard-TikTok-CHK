import { useMemo, useState } from 'react'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, ErrorAlert, Hint, Kpi, NumberField } from '@/components/analytics/ui'
import { ChartCard } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { count } from '@/lib/format'
import { MAX_SHORTLIST, type CandidateCreator, type MarketCreator, tiktokCreatorMarket } from '@/lib/tiktokCreatorMarket'
import { useAsync } from '@/lib/useAsync'

import { CsvButton, PageTitle } from '../walmart/walmartUi'
import { clip } from './tiktokCatalogFormat'
import { NO_VALUE } from './tiktokFormat'
import {
  CANDIDATE_SCORE,
  SCORE_MAX,
  SCORE_WEIGHTS,
  TAB_CREATORS,
  WATCH_SCORE,
  candidateReason,
  categoriesText,
  displayName,
  followersText,
  gmvText,
  handleText,
  partLabel,
  topParts,
} from './tiktokMarketFormat'
import { MarketCreatorPanel } from './tiktokMarketPanel'
import { CreatorAvatar, MarketBadges, MarketCoverageNotice, MarketFootnotes, MarketScoreBar, PartBars } from './tiktokMarketUi'

const DEFAULT_SHORTLIST = 10
const NAME_MAX = 22
const CHART_LABEL_WIDTH = 150

function CandidateCard({ c, rank, onOpen }: { c: CandidateCreator; rank: number; onOpen: (c: MarketCreator) => void }) {
  const best = topParts(c.score_parts)
  return (
    <li>
      <button type="button" onClick={() => onOpen(c)} className="flex w-full flex-col gap-3 rounded-lg border p-3 text-left hover:shadow-md focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none sm:flex-row">
        <span className="flex items-center gap-3 sm:w-64 sm:shrink-0">
          <span className="text-muted-foreground w-6 text-sm tabular-nums">{rank}</span>
          <CreatorAvatar c={c} />
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">{displayName(c)}</span>
            <span className="text-muted-foreground block truncate text-xs">{handleText(c)}</span>
            <span className="text-muted-foreground block text-xs">
              {followersText(c)} seguidores · GMV {gmvText(c)}
            </span>
          </span>
        </span>
        <span className="min-w-0 flex-1 space-y-2">
          <MarketBadges c={c} />
          <MarketScoreBar score={c.score} />
          <span className="block text-sm">{candidateReason(c)}</span>
          <span className="text-muted-foreground block text-xs">
            Categorías: {categoriesText(c)}. Suma más en {best.map((p) => partLabel(p.name).toLowerCase()).join(' y ')}.
          </span>
        </span>
        <PartBars parts={c.score_parts} className="sm:w-56 sm:shrink-0" />
      </button>
    </li>
  )
}

function ScoreExplainer() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Cómo se calcula el puntaje</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>
          El puntaje va de 0 a {SCORE_MAX} y es la suma de tres partes. Desde {CANDIDATE_SCORE} puntos un creador es &quot;Candidato&quot;; entre {WATCH_SCORE} y {CANDIDATE_SCORE}, &quot;Vigilar&quot;; por debajo, &quot;Bajo ajuste&quot;.
        </p>
        <ul className="space-y-2">
          {SCORE_WEIGHTS.map((w) => (
            <li key={w.name} className="rounded-md border p-2">
              <span className="font-medium">
                {w.label} (hasta {w.weight})
              </span>
              <p className="text-muted-foreground text-xs">{w.what}</p>
            </li>
          ))}
        </ul>
        <p className="text-muted-foreground text-xs">
          Anti-recomendación: no entra a la lista quien ya recibió muestras y no vendió, quien la política de muestras pausó o cortó, ni quien ya vende con nosotros. La lista solo incluye creadores nuevos, vistos después de la carga inicial.
        </p>
      </CardContent>
    </Card>
  )
}

function EmptyShortlist({ onOpenTab }: { onOpenTab?: (tab: string) => void }) {
  return (
    <Callout tone="info">
      <p className="mb-1 font-medium">Todavía no hay candidatos nuevos, y es lo esperado</p>
      <p>
        Hoy se hizo la carga inicial del mercado: todos los creadores que entraron en ella cuentan como ya conocidos, no como nuevos. Cuando en las próximas sincronizaciones aparezcan creadores que no estaban y encajen con nuestro negocio, se listarán aquí con la razón de cada uno.
      </p>
      {onOpenTab && (
        <button type="button" onClick={() => onOpenTab(TAB_CREATORS)} className="mt-2 text-xs underline hover:no-underline focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
          Mientras tanto, busca entre todos los creadores cargados
        </button>
      )}
    </Callout>
  )
}

const csvRow = (c: CandidateCreator, i: number) => ({
  Posición: i + 1,
  Usuario: c.username,
  Apodo: c.nickname,
  Seguidores: c.followers,
  Categorías: c.categories.join(' | '),
  'GMV (rango o importe)': gmvText(c),
  Puntaje: c.score,
  Razón: candidateReason(c),
})

export function TikTokMarketCandidates({ onOpenTab }: { onOpenTab?: (tab: string) => void }) {
  const scope = useScopeDescription()
  const [limit, setLimit] = useState(DEFAULT_SHORTLIST)
  const status = useAsync(() => tiktokCreatorMarket.status(), [])
  const result = useAsync(() => tiktokCreatorMarket.candidates(limit), [limit])
  const [selected, setSelected] = useState<MarketCreator | null>(null)

  const rows = useMemo(() => result.data?.rows ?? [], [result.data?.rows])
  const chartRows = useMemo<HBarRow[]>(() => rows.map((c) => ({ id: c.creator_open_id, label: clip(displayName(c), NAME_MAX), score: c.score })), [rows])
  const total = result.data?.total ?? 0
  const best = rows[0]

  return (
    <div className="space-y-6">
      <PageTitle title="Candidatos recomendados" scope={scope} note="Los mejores creadores nuevos para colaborar, con la razón de cada uno. Solo lectura." />

      <ErrorAlert message={status.error ?? result.error} />
      <MarketCoverageNotice status={status.data} />

      <div className="grid gap-4 sm:grid-cols-3">
        <Kpi title="Candidatos nuevos" value={result.data ? count(total) : NO_VALUE} hint="creadores nuevos con buen puntaje y sin anti-recomendación" />
        <Kpi title="Mostrando" value={result.data ? count(rows.length) : NO_VALUE} hint={`de un máximo de ${MAX_SHORTLIST}`} />
        <Kpi title="Mejor puntaje" value={best ? best.score.toFixed(0) : NO_VALUE} hint={best ? displayName(best) : undefined} />
      </div>

      {result.loading && !result.data && <div className="text-muted-foreground text-sm">Cargando…</div>}

      {result.data && rows.length === 0 && <EmptyShortlist onOpenTab={onOpenTab} />}

      {rows.length > 0 && (
        <>
          <Card>
            <CardHeader className="space-y-3">
              <CardTitle>Shortlist</CardTitle>
              <div className="flex flex-wrap items-end gap-3">
                <NumberField label="Cuántos mostrar" value={limit} onCommit={setLimit} min={1} max={MAX_SHORTLIST} />
                <CsvButton filename="tiktok-mercado-candidatos.csv" rows={rows.map(csvRow)} />
              </div>
            </CardHeader>
            <CardContent>
              <ol className="space-y-3" aria-busy={result.loading}>
                {rows.map((c, i) => (
                  <CandidateCard key={c.creator_open_id} c={c} rank={i + 1} onOpen={setSelected} />
                ))}
              </ol>
            </CardContent>
          </Card>

          <ChartCard title="Comparación de puntajes" subtitle="Puntaje de candidato (0 a 100) de cada creador de la lista." filename="tiktok-mercado-candidatos-puntaje" csv={rows.map(csvRow)}>
            <HBarChart rows={chartRows} series={[{ key: 'score', label: 'Puntaje' }]} format={(v) => v.toFixed(0)} refLine={CANDIDATE_SCORE} labelWidth={CHART_LABEL_WIDTH} />
          </ChartCard>
        </>
      )}

      <ScoreExplainer />
      <Hint>La línea de referencia del gráfico marca el puntaje desde el que un creador es candidato ({CANDIDATE_SCORE}).</Hint>
      <MarketFootnotes />

      {selected && <MarketCreatorPanel c={selected} onClose={() => setSelected(null)} onOpenTab={onOpenTab} />}
    </div>
  )
}
