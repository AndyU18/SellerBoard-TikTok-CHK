import { useMemo } from 'react'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, ErrorAlert, Hint, Kpi, pct1 } from '@/components/analytics/ui'
import { type BubblePoint, BubbleChart } from '@/components/charts/BubbleChart'
import { ChartCard, EmptyChart, axisNumber } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { count } from '@/lib/format'
import { MAX_LIMIT, type MarketCreator, type MarketStatus, type MarketSummary, tiktokCreatorMarket } from '@/lib/tiktokCreatorMarket'
import { useAsync } from '@/lib/useAsync'

import { PageTitle } from '../walmart/walmartUi'
import { clip } from './tiktokCatalogFormat'
import { shortDateTime } from './tiktokFormat'
import {
  ENGAGEMENT_NOTE,
  SCORE_TIER_LABEL,
  SIZE_TIERS,
  TAB_CANDIDATES,
  TAB_COLLABORATIONS,
  TAB_CREATORS,
  displayName,
  exactGmvShare,
  followersText,
  gmvText,
  handleText,
  scoreTier,
  sizeLabel,
} from './tiktokMarketFormat'
import { CreatorAvatar, MarketBadges, MarketCoverageNotice, MarketFootnotes, MarketScoreBar } from './tiktokMarketUi'

const MAX_CATEGORIES = 12
const CATEGORY_LABEL_MAX = 24
const FRACTION_TO_PERCENT = 100
const SIZE_LABEL_WIDTH = 170
const CATEGORY_LABEL_WIDTH = 150
const SHORTCUTS: { tab: string; title: string; text: string }[] = [
  { tab: TAB_CREATORS, title: 'Buscar creadores', text: 'Filtra y compara todos los creadores cargados.' },
  { tab: TAB_CANDIDATES, title: 'Candidatos recomendados', text: 'La shortlist de nuevos creadores con su razón.' },
  { tab: TAB_COLLABORATIONS, title: 'Colaboraciones', text: 'Las abiertas y las dirigidas de nuestra tienda.' },
]

function bubblePoints(creators: MarketCreator[]): BubblePoint[] {
  return creators
    .filter((c) => c.engagement_rate != null)
    .map((c) => ({
      id: c.creator_open_id,
      label: displayName(c),
      x: c.followers,
      y: (c.engagement_rate ?? 0) * FRACTION_TO_PERCENT,
      z: c.score,
      group: SCORE_TIER_LABEL[scoreTier(c.score)],
    }))
}

function Distributions({ s }: { s: MarketSummary }) {
  const categories = useMemo<HBarRow[]>(() => s.by_category.slice(0, MAX_CATEGORIES).map((c) => ({ id: c.category, label: clip(c.category, CATEGORY_LABEL_MAX), creators: c.creators })), [s.by_category])
  const sizes = useMemo<HBarRow[]>(() => {
    const byTier = new Map(s.by_size.map((t) => [t.tier, t.creators]))
    return SIZE_TIERS.map((t) => ({ id: t.tier, label: sizeLabel(t.tier), creators: byTier.get(t.tier) ?? 0 }))
  }, [s.by_size])
  const series = [{ key: 'creators', label: 'Creadores' }]
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <ChartCard title="Creadores por categoría" subtitle={`Las ${MAX_CATEGORIES} categorías con más creadores; un creador puede estar en varias.`} filename="tiktok-mercado-categorias" csv={s.by_category.map((c) => ({ Categoría: c.category, Creadores: c.creators }))}>
        <HBarChart rows={categories} series={series} format={count} labelWidth={CATEGORY_LABEL_WIDTH} />
      </ChartCard>
      <ChartCard title="Creadores por tamaño" subtitle="Tramos por seguidores; los medianos son los que más rinden por muestra." filename="tiktok-mercado-tamano" csv={sizes.map((r) => ({ Tamaño: r.label, Creadores: r.creators }))}>
        <HBarChart rows={sizes} series={series} format={count} labelWidth={SIZE_LABEL_WIDTH} />
      </ChartCard>
    </div>
  )
}

function TopList({ items }: { items: MarketCreator[] }) {
  return (
    <ChartCard
      title="Top 10 candidatos"
      subtitle="Mejor puntaje entre los candidatos (nuevos o no)."
      filename="tiktok-mercado-top10"
      csv={items.map((c) => ({ Usuario: c.username, Seguidores: c.followers, 'GMV (rango o importe)': gmvText(c), Puntaje: c.score }))}
    >
      {items.length === 0 ? (
        <EmptyChart>Todavía no hay candidatos.</EmptyChart>
      ) : (
        <ol className="space-y-2">
          {items.map((c, i) => (
            <li key={c.creator_open_id} className="flex items-center gap-3 rounded-md border p-2">
              <span className="text-muted-foreground w-5 text-sm tabular-nums">{i + 1}</span>
              <CreatorAvatar c={c} className="size-10" />
              <span className="min-w-0 flex-1 space-y-1">
                <span className="block truncate text-sm font-medium">
                  {displayName(c)} <span className="text-muted-foreground text-xs font-normal">{handleText(c)}</span>
                </span>
                <MarketBadges c={c} />
              </span>
              <span className="hidden w-20 text-right text-xs sm:block">{followersText(c)}</span>
              <MarketScoreBar score={c.score} className="w-28" />
            </li>
          ))}
        </ol>
      )}
    </ChartCard>
  )
}

function SyncStatus({ status }: { status: MarketStatus }) {
  const { coverage, last_sync: sync, routes, unavailable } = status
  const facts: [string, string][] = [
    ['Creadores cargados', count(coverage.creators)],
    ['Activos', count(coverage.creators_active)],
    ['Con GMV exacto', `${count(coverage.creators_with_gmv)} (${pct1(exactGmvShare(status))})`],
    ['Con ficha leída (engagement)', count(coverage.creators_with_detail)],
    ['Colaboraciones abiertas', count(coverage.open_collaborations)],
    ['Colaboraciones dirigidas', count(coverage.target_collaborations)],
  ]
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Cobertura y sincronización</CardTitle>
        <p className="text-muted-foreground text-xs">
          Última sincronización: {sync ? `${shortDateTime(sync.finished_at ?? sync.started_at)} (${sync.status})` : 'ninguna registrada'}.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          {facts.map(([label, value]) => (
            <div key={label}>
              <dt className="text-muted-foreground text-xs">{label}</dt>
              <dd className="font-medium">{value}</dd>
            </div>
          ))}
        </dl>
        <div>
          <h3 className="mb-1 text-sm font-semibold">Rutas disponibles</h3>
          <ul className="space-y-0.5 text-xs">
            {Object.entries(routes).map(([name, r]) => (
              <li key={name}>
                <span className="font-medium">{name}</span> <span className="text-muted-foreground">{r.method} {r.path} (versión {r.version})</span>
              </li>
            ))}
          </ul>
        </div>
        {unavailable.length > 0 && (
          <Callout tone="info">
            <p className="mb-1 font-medium">Lo que no se pudo conseguir</p>
            <ul className="list-disc space-y-0.5 pl-5 text-xs">
              {unavailable.map((u) => (
                <li key={u}>{u}</li>
              ))}
            </ul>
          </Callout>
        )}
      </CardContent>
    </Card>
  )
}

function Shortcuts({ onOpenTab }: { onOpenTab?: (tab: string) => void }) {
  if (!onOpenTab) return null
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {SHORTCUTS.map((s) => (
        <button key={s.tab} type="button" onClick={() => onOpenTab(s.tab)} className="rounded-lg border p-3 text-left hover:shadow-md focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
          <span className="block text-sm font-medium">{s.title}</span>
          <span className="text-muted-foreground block text-xs">{s.text}</span>
        </button>
      ))}
    </div>
  )
}

export function TikTokMarketInsights({ onOpenTab }: { onOpenTab?: (tab: string) => void }) {
  const scope = useScopeDescription()
  const status = useAsync(() => tiktokCreatorMarket.status(), [])
  const summary = useAsync(() => tiktokCreatorMarket.summary(), [])
  const sample = useAsync(() => tiktokCreatorMarket.marketplace({ sort: 'score', limit: MAX_LIMIT, offset: 0 }), [])

  const s = summary.data
  const points = useMemo(() => bubblePoints(sample.data?.rows ?? []), [sample.data?.rows])

  return (
    <div className="space-y-6">
      <PageTitle title="Panorama del mercado" scope={scope} note="Mercado de creadores afiliados de TikTok Shop (datos públicos). Solo lectura." />

      <ErrorAlert message={status.error ?? summary.error ?? sample.error} />
      {summary.loading && !s && <div className="text-muted-foreground text-sm">Cargando…</div>}
      <MarketCoverageNotice status={status.data} />

      {s && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi title="Creadores cargados" value={count(s.creators)} />
            <Kpi title="Candidatos fuertes" value={count(s.candidates)} />
            <Kpi title="Nuevos esta semana" value={count(s.new)} hint={s.new === 0 ? 'carga inicial: aún no hay nuevos' : undefined} />
            <Kpi title="A evitar" value={count(s.excluded)} className={s.excluded > 0 ? 'text-red-600' : ''} />
          </div>

          <Shortcuts onOpenTab={onOpenTab} />
          <Distributions s={s} />

          <ChartCard
            title="Seguidores frente a engagement*"
            subtitle={`Cada burbuja es un creador con ficha leída (entre los ${MAX_LIMIT} de mejor puntaje): el tamaño y el color indican su puntaje.`}
            filename="tiktok-mercado-burbujas"
            csv={points.map((p) => ({ Creador: p.label, Seguidores: p.x, 'Engagement (%) (unidad asumida)': Number(p.y.toFixed(2)), Puntaje: p.z, Grupo: p.group }))}
          >
            <BubbleChart points={points} xLabel="Seguidores" yLabel="Engagement*" zLabel="Puntaje" xFormat={axisNumber} yFormat={(v) => `${v.toFixed(0)} %`} zFormat={(v) => v.toFixed(0)} />
          </ChartCard>
          <Hint>{ENGAGEMENT_NOTE}</Hint>

          <TopList items={s.top_candidates} />
        </>
      )}

      {status.data && <SyncStatus status={status.data} />}
      <MarketFootnotes />
    </div>
  )
}
