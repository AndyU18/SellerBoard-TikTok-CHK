import { useMemo, useState } from 'react'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, ErrorAlert, Hint, Kpi, pct1, Segmented } from '@/components/analytics/ui'
import { type BubblePoint, BubbleChart } from '@/components/charts/BubbleChart'
import { ComboChart } from '@/components/charts/ComboChart'
import { ChartCard, axisNumber, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { count, money } from '@/lib/format'
import {
  type CategorySummary,
  type ChangeRow,
  type CreatorOpportunity,
  type HistoryChanges,
  MAX_LIMIT,
  type OpportunitiesResponse,
  tiktokCreator,
} from '@/lib/tiktokCreatorCatalog'
import { useAsync } from '@/lib/useAsync'

import { PageTitle } from '../walmart/walmartUi'
import { clip } from './tiktokCatalogFormat'
import { ProductImage } from './tiktokCatalogUi'
import { NO_CATEGORY, OPPORTUNITIES_TAB, rateText, relatesToOurCatalog, storePendingCategory } from './tiktokCreatorFormat'
import { CreatorProductPanel } from './tiktokCreatorPanel'
import { CoverageNotice, OpportunityBadges, ScoreBar } from './tiktokCreatorUi'
import { NO_VALUE, shortDate } from './tiktokFormat'

type CategoryMetric = 'products' | 'commission'

const MAX_CATEGORIES = 12
const CATEGORY_LABEL_MAX = 24
const TITLE_MAX = 48
const FRACTION_TO_PERCENT = 100
const CATEGORY_METRICS: { value: CategoryMetric; label: string }[] = [
  { value: 'products', label: 'Cantidad de productos' },
  { value: 'commission', label: 'Comisión promedio' },
]

/** Tramos de comisión en puntos porcentuales: [desde, hasta). El último no tiene tope. */
const COMMISSION_BUCKETS: { from: number; to: number; label: string }[] = [
  { from: 0, to: 5, label: 'Menos de 5 %' },
  { from: 5, to: 10, label: '5 – 10 %' },
  { from: 10, to: 15, label: '10 – 15 %' },
  { from: 15, to: 20, label: '15 – 20 %' },
  { from: 20, to: 30, label: '20 – 30 %' },
  { from: 30, to: Infinity, label: '30 % o más' },
]

const GROUP_EQUIVALENT = 'Ya vendemos algo así'
const GROUP_AFFINE = 'Afín a nuestro negocio'
const GROUP_OTHER = 'Sin relación'

const affinityGroup = (p: CreatorOpportunity): string => (p.relation.equivalent ? GROUP_EQUIVALENT : p.affinity_groups.length > 0 || relatesToOurCatalog(p) ? GROUP_AFFINE : GROUP_OTHER)

function commissionHistogram(items: CreatorOpportunity[]) {
  return COMMISSION_BUCKETS.map((b) => ({
    bucket: b.label,
    products: items.filter((p) => {
      const percent = (p.commission_rate ?? NaN) * FRACTION_TO_PERCENT
      return percent >= b.from && percent < b.to
    }).length,
  }))
}

function bubblePoints(items: CreatorOpportunity[]): BubblePoint[] {
  return items
    .filter((p) => !p.suspect && p.commission_rate != null && p.price != null)
    .map((p) => ({
      id: p.product_id,
      label: clip(p.title ?? p.product_id, TITLE_MAX),
      x: (p.commission_rate ?? 0) * FRACTION_TO_PERCENT,
      y: p.units_sold,
      z: p.price ?? undefined,
      group: affinityGroup(p),
    }))
}

function CategoryChart({ categories, onSelect }: { categories: CategorySummary[]; onSelect?: (category: string) => void }) {
  const [metric, setMetric] = useState<CategoryMetric>('products')
  const rows = useMemo<HBarRow[]>(
    () =>
      categories.slice(0, MAX_CATEGORIES).map((c) => ({
        id: c.category,
        label: clip(c.category, CATEGORY_LABEL_MAX),
        products: c.products,
        commission: c.avg_commission_rate,
      })),
    [categories],
  )
  const isProducts = metric === 'products'
  return (
    <ChartCard
      title="Oportunidades por categoría"
      subtitle={onSelect ? 'Un clic en una barra abre las oportunidades de esa categoría.' : `Las ${MAX_CATEGORIES} categorías con más productos.`}
      filename="tiktok-creator-categorias"
      csv={categories.map((c) => ({ Categoría: c.category, Productos: c.products, 'Comisión promedio (%)': c.avg_commission_rate == null ? null : Number((c.avg_commission_rate * FRACTION_TO_PERCENT).toFixed(2)), 'Afines a nuestro negocio': c.affine }))}
      actions={<Segmented value={metric} options={CATEGORY_METRICS} onChange={setMetric} label="Métrica por categoría" />}
    >
      <HBarChart
        rows={rows}
        series={isProducts ? [{ key: 'products', label: 'Productos' }] : [{ key: 'commission', label: 'Comisión promedio' }]}
        format={isProducts ? count : (v) => pct1(v)}
        labelWidth={150}
        onRowClick={onSelect ? (row) => row.id !== NO_CATEGORY && onSelect(row.id) : undefined}
      />
    </ChartCard>
  )
}

const arrow = (pct: number | null) => (pct == null || pct === 0 ? { symbol: '•', tone: 'text-muted-foreground' } : pct > 0 ? { symbol: '▲', tone: 'text-emerald-600' } : { symbol: '▼', tone: 'text-red-600' })

function ChangeList({ title, rows, total, format }: { title: string; rows: ChangeRow[]; total: number; format: (v: number | null) => string }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold">
        {title} ({count(total)})
      </h3>
      {rows.length === 0 ? (
        <p className="text-muted-foreground text-sm">Sin cambios.</p>
      ) : (
        <ul className="divide-y text-sm">
          {rows.map((r) => {
            const { symbol, tone } = arrow(r.change_pct)
            return (
              <li key={r.product_id} className="flex items-center justify-between gap-3 py-1.5">
                <span className="min-w-0 flex-1 truncate">{clip(r.title ?? r.product_id, TITLE_MAX)}</span>
                <span className="text-muted-foreground text-xs whitespace-nowrap tabular-nums">
                  {format(r.old)} → {format(r.new)}
                </span>
                <span className={`w-20 text-right font-medium whitespace-nowrap tabular-nums ${tone}`}>
                  <span aria-hidden>{symbol} </span>
                  {r.change_pct == null ? NO_VALUE : `${r.change_pct > 0 ? '+' : ''}${r.change_pct.toFixed(1)} %`}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function ChangesBlock({ changes }: { changes: HistoryChanges }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cambios desde la foto anterior</CardTitle>
        {changes.available && changes.from && changes.to && (
          <p className="text-muted-foreground text-xs">
            Entre las fotos semanales del {shortDate(changes.from)} y del {shortDate(changes.to)}. Se muestran los cambios más grandes.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {changes.available ? (
          <div className="grid gap-6 lg:grid-cols-2">
            <ChangeList title="Precio" rows={changes.price} total={changes.price_count ?? changes.price.length} format={(v) => money(v)} />
            <ChangeList title="Comisión" rows={changes.commission} total={changes.commission_count ?? changes.commission.length} format={(v) => pct1(v)} />
          </div>
        ) : (
          <EmptyChart>{changes.reason ?? 'Todavía no hay dos fotos semanales para comparar.'}</EmptyChart>
        )}
      </CardContent>
    </Card>
  )
}

function TopList({ items, onOpen }: { items: CreatorOpportunity[]; onOpen: (p: CreatorOpportunity) => void }) {
  return (
    <ChartCard
      title="Top 10 oportunidades"
      subtitle="Mejor puntaje del catálogo cargado; un clic muestra el detalle."
      filename="tiktok-creator-top10"
      csv={items.map((p) => ({ Producto: p.title, Categoría: p.category, 'Comisión (%)': p.commission_rate == null ? null : Number((p.commission_rate * FRACTION_TO_PERCENT).toFixed(2)), Puntaje: p.score }))}
    >
      {items.length === 0 ? (
        <EmptyChart />
      ) : (
        <ol className="space-y-2">
          {items.map((p, i) => (
            <li key={p.product_id}>
              <button type="button" onClick={() => onOpen(p)} className="flex w-full items-center gap-3 rounded-md border p-2 text-left hover:shadow-md focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
                <span className="text-muted-foreground w-5 text-sm tabular-nums">{i + 1}</span>
                <ProductImage src={p.image_url} title={p.title} className="size-12" />
                <span className="min-w-0 flex-1 space-y-1">
                  <span className="block truncate text-sm font-medium">{p.title ?? p.product_id}</span>
                  <OpportunityBadges p={p} />
                </span>
                <span className="hidden w-16 text-right text-sm tabular-nums sm:block">{rateText(p)}</span>
                <ScoreBar score={p.score} className="w-28" />
              </button>
            </li>
          ))}
        </ol>
      )}
    </ChartCard>
  )
}

function Limitations({ suspect, sample }: { suspect: number; sample: OpportunitiesResponse | null }) {
  return (
    <Callout tone="info">
      <p className="mb-1 font-medium">Limitaciones de esta lectura</p>
      <ul className="list-disc space-y-0.5 pl-5">
        <li>El catálogo puede estar cargado solo en parte: TikTok entrega 20 productos por consulta y muestra como máximo 10.000.</li>
        <li>Se excluyeron {count(suspect)} producto(s) con datos de prueba (precios o unidades imposibles); no entran en ningún cálculo.</li>
        <li>
          La distribución de comisiones y el mapa de burbujas usan los {count(Math.min(MAX_LIMIT, sample?.total ?? MAX_LIMIT))} productos con mejor puntaje, no todo el catálogo.
        </li>
        <li>Las unidades vendidas son acumuladas desde que existe el producto, no del último mes.</li>
      </ul>
    </Callout>
  )
}

export function TikTokCreatorInsights({ onOpenTab }: { onOpenTab?: (tab: string) => void }) {
  const scope = useScopeDescription()
  const status = useAsync(() => tiktokCreator.status(), [])
  const summary = useAsync(() => tiktokCreator.summary(), [])
  const sample = useAsync(() => tiktokCreator.opportunities({ sort: 'score', limit: MAX_LIMIT, offset: 0, includeSuspect: false }), [])
  const [selected, setSelected] = useState<CreatorOpportunity | null>(null)

  const s = summary.data
  const items = useMemo(() => sample.data?.items ?? [], [sample.data?.items])
  const histogram = useMemo(() => commissionHistogram(items), [items])
  const points = useMemo(() => bubblePoints(items), [items])

  const openCategory = (category: string) => {
    storePendingCategory(category)
    onOpenTab?.(OPPORTUNITIES_TAB)
  }

  return (
    <div className="space-y-6">
      <PageTitle title="Panorama del catálogo de afiliados" scope={scope} note="Cuenta de creador propia. Solo lectura." />

      <ErrorAlert message={status.error ?? summary.error ?? sample.error} />
      {(summary.loading || sample.loading) && !s && <div className="text-muted-foreground text-sm">Cargando…</div>}
      <CoverageNotice status={status.data} />

      {s && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi title="Productos analizados" value={count(s.products)} hint="cargados, sin datos de prueba" />
            <Kpi title="Comisión promedio" value={pct1(s.avg_commission_rate)} />
            <Kpi title="Nuevos esta semana" value={count(s.new_this_week.count)} />
            <Kpi title="Datos sospechosos excluidos" value={count(s.suspect_products)} className={s.suspect_products > 0 ? 'text-red-600' : ''} />
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <CategoryChart categories={s.by_category} onSelect={onOpenTab ? openCategory : undefined} />
            <ChartCard
              title="Distribución de comisiones"
              subtitle="Cuántos productos hay en cada tramo de comisión (los 200 mejores por puntaje)."
              filename="tiktok-creator-comisiones"
              csv={histogram.map((h) => ({ Tramo: h.bucket, Productos: h.products }))}
            >
              <ComboChart data={histogram} xKey="bucket" series={[{ key: 'products', label: 'Productos', type: 'bar' }]} leftFormat={count} />
            </ChartCard>
          </div>

          <ChartCard
            title="Comisión frente a unidades vendidas"
            subtitle="Cada burbuja es un producto: el tamaño es el precio y el color indica su afinidad con nuestro negocio."
            filename="tiktok-creator-burbujas"
            csv={points.map((p) => ({ Producto: p.label, 'Comisión (%)': Number(p.x.toFixed(2)), Unidades: p.y, Precio: p.z, Afinidad: p.group }))}
          >
            <BubbleChart points={points} xLabel="Comisión" yLabel="Unidades vendidas" zLabel="Precio" xFormat={(v) => `${v.toFixed(0)} %`} yFormat={axisNumber} zFormat={money} />
          </ChartCard>

          <div className="grid gap-4 xl:grid-cols-2">
            <TopList items={s.top} onOpen={setSelected} />
            <ChangesBlock changes={s.changes} />
          </div>

          <Limitations suspect={s.suspect_products} sample={sample.data} />
          <Hint>Las flechas verdes indican subida y las rojas bajada respecto a la foto semanal anterior.</Hint>
        </>
      )}

      {selected && sample.data && <CreatorProductPanel p={selected} formula={sample.data.score_formula} constants={sample.data.constants} onClose={() => setSelected(null)} />}
    </div>
  )
}
