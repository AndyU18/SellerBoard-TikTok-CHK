import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, pct1, Pill } from '@/components/analytics/ui'
import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { BubbleChart, type BubblePoint } from '@/components/charts/BubbleChart'
import { axisPct, ChartCard, COLOR, EmptyChart, type CsvRow } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { tiktokPromos, type PromoActivity } from '@/lib/tiktokPromos'
import { useAsync } from '@/lib/useAsync'
import { CsvButton, nextSort, SearchBox, SortHead, type SortState } from '@/pages/walmart/walmartUi'
import { compareValues } from '@/pages/walmart/walmartSort'

import { PromoDetail } from './PromoDetail'
import { PromoGantt } from './PromoGantt'
import { promoGroup, statusStyle } from './promoStatus'
import { average, humanize, NO_VALUE, parsePercent, shortDate, toMs } from './tiktokFormat'

const TOP_RANKING = 12
const LABEL_MAX = 34

type SortKey = 'title' | 'type' | 'status' | 'begin' | 'end' | 'products' | 'uplift'
const TEXT_KEYS: readonly SortKey[] = ['title', 'type', 'status']
const INITIAL_SORT: SortState<SortKey> = { key: 'begin', dir: -1 }

const clip = (text: string) => (text.length > LABEL_MAX ? `${text.slice(0, LABEL_MAX - 1)}…` : text)
const nameOf = (a: PromoActivity) => a.title ?? a.activity_id
const uplift = (a: PromoActivity) => a.roi?.uplift ?? null

/** Descuento de la actividad como fracción: el suyo, o el promedio del de sus productos. */
function discountOf(a: PromoActivity): number | null {
  return parsePercent(a.discount) ?? average(a.products.map((p) => parsePercent(p.discount)).filter((v): v is number => v != null))
}

function sortValue(a: PromoActivity, key: SortKey): string | number | null {
  switch (key) {
    case 'title':
      return nameOf(a).toLowerCase()
    case 'type':
      return a.type
    case 'status':
      return statusStyle(a.status).label
    case 'begin':
      return toMs(a.begin_at)
    case 'end':
      return toMs(a.end_at)
    case 'products':
      return a.product_count
    case 'uplift':
      return uplift(a)
  }
}

function csvRows(rows: PromoActivity[]): CsvRow[] {
  return rows.map((a) => ({
    actividad: nameOf(a),
    tipo: humanize(a.type),
    estado: statusStyle(a.status).label,
    inicio: a.begin_at,
    fin: a.end_at,
    productos: a.product_count,
    descuento: a.discount,
    unidades_dia_durante: a.roi?.units_per_day_during,
    unidades_dia_antes: a.roi?.units_per_day_before,
    aumento_unidades: a.roi?.uplift == null ? '' : a.roi.uplift.toFixed(4),
    ventas_durante: a.roi?.sales_during,
    alcance_roi: a.roi_scope === 'shop' ? 'tienda' : 'productos',
  }))
}

function PromotionsTable({ rows, selectedId, onSelect }: { rows: PromoActivity[]; selectedId: string | null; onSelect: (id: string) => void }) {
  const [sort, setSort] = useState<SortState<SortKey>>(INITIAL_SORT)
  const [search, setSearch] = useState('')
  const onSort = (key: SortKey) => setSort((cur) => nextSort(cur, key, TEXT_KEYS))

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase()
    return rows
      .filter((a) => !q || `${nameOf(a)} ${humanize(a.type)}`.toLowerCase().includes(q))
      .sort((a, b) => compareValues(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir))
  }, [rows, search, sort])

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="text-base">Actividades ({count(shown.length)})</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <SearchBox value={search} onChange={setSearch} placeholder="Buscar actividad o tipo" />
          <CsvButton filename="tiktok-promociones.csv" rows={csvRows(shown)} />
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <SortHead sortKey="title" label="Actividad" sort={sort} onSort={onSort} right={false} />
              <SortHead sortKey="type" label="Tipo" sort={sort} onSort={onSort} right={false} />
              <SortHead sortKey="status" label="Estado" sort={sort} onSort={onSort} right={false} />
              <SortHead sortKey="begin" label="Inicio" sort={sort} onSort={onSort} />
              <SortHead sortKey="end" label="Fin" sort={sort} onSort={onSort} />
              <SortHead sortKey="products" label="Productos" sort={sort} onSort={onSort} />
              <TableHead className="text-right">Unid./día (antes → durante)</TableHead>
              <SortHead sortKey="uplift" label="ROI" sort={sort} onSort={onSort} />
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((a) => {
              const style = statusStyle(a.status)
              const up = uplift(a)
              return (
                <TableRow key={a.activity_id} data-state={selectedId === a.activity_id ? 'selected' : undefined}>
                  <TableCell className="max-w-xs font-medium">
                    <button
                      type="button"
                      onClick={() => onSelect(a.activity_id)}
                      aria-pressed={selectedId === a.activity_id}
                      className="max-w-full truncate rounded text-left hover:underline focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none"
                      title={nameOf(a)}
                    >
                      {nameOf(a)}
                    </button>
                  </TableCell>
                  <TableCell>{humanize(a.type)}</TableCell>
                  <TableCell>
                    <Pill label={style.label} className={style.pill} />
                  </TableCell>
                  <TableCell className="text-right whitespace-nowrap">{shortDate(a.begin_at)}</TableCell>
                  <TableCell className="text-right whitespace-nowrap">{shortDate(a.end_at)}</TableCell>
                  <TableCell className="text-right">{a.roi_scope === 'shop' && !a.product_count ? 'Tienda' : count(a.product_count)}</TableCell>
                  <TableCell className="text-right whitespace-nowrap">
                    {a.roi ? `${a.roi.units_per_day_before.toFixed(2)} → ${a.roi.units_per_day_during.toFixed(2)}` : NO_VALUE}
                  </TableCell>
                  <TableCell className={`text-right font-medium ${up == null ? '' : up >= 0 ? 'text-green-700' : 'text-red-700'}`}>{pct1(up)}</TableCell>
                </TableRow>
              )
            })}
            {shown.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="text-muted-foreground text-center">
                  Ninguna actividad coincide con la búsqueda.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

/** Actividades promocionales de TikTok Shop: línea de tiempo, ROI observacional y productos incluidos. */
export function TikTokPromotions() {
  const { preset } = useScope()
  const description = useScopeDescription()
  const days = daysFromPreset(preset)
  const data = useAsync(() => tiktokPromos.promotions(days), [days])
  const report = data.data
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const activities = useMemo(() => report?.activities ?? [], [report])
  const selected = activities.find((a) => a.activity_id === selectedId) ?? null
  const toggle = (id: string) => setSelectedId((cur) => (cur === id ? null : id))

  const groups = useMemo(() => {
    const by = { active: 0, scheduled: 0, ended: 0 }
    for (const a of activities) by[promoGroup(a.status)] += 1
    return by
  }, [activities])
  const avgRoi = useMemo(() => average(activities.map(uplift).filter((v): v is number => v != null)), [activities])

  const ranking = useMemo<HBarRow[]>(
    () =>
      activities
        .filter((a) => uplift(a) != null)
        .sort((a, b) => (uplift(b) ?? 0) - (uplift(a) ?? 0))
        .slice(0, TOP_RANKING)
        .map((a) => ({ id: a.activity_id, label: clip(nameOf(a)), uplift: uplift(a) })),
    [activities],
  )

  const bubbles = useMemo<BubblePoint[]>(
    () =>
      activities.flatMap((a) => {
        const x = discountOf(a)
        const y = uplift(a)
        return x == null || y == null ? [] : [{ id: a.activity_id, label: nameOf(a), x, y, z: a.roi?.sales_during ?? 0, group: statusStyle(a.status).label }]
      }),
    [activities],
  )

  const loading = data.loading && !report
  const placeholder = loading ? 'Cargando…' : NO_VALUE

  return (
    <div className="space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Promociones de TikTok Shop</h1>
        <p className="text-muted-foreground text-sm">{description}</p>
        <Hint>
          Actividades de TikTok Shop. El ROI compara unidades por día durante la actividad contra los {report?.summary.baseline_days ?? 14} días previos.
        </Hint>
      </header>

      <ErrorAlert message={data.error} />

      <section aria-label="Indicadores" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Actividades activas" value={report ? count(groups.active) : placeholder} hint={report ? `${count(report.summary.activities)} en el período` : undefined} />
        <Kpi title="Programadas" value={report ? count(groups.scheduled) : placeholder} />
        <Kpi title="Terminadas" value={report ? count(groups.ended) : placeholder} />
        <Kpi
          title="ROI promedio"
          value={report ? pct1(avgRoi) : placeholder}
          hint={report ? 'Aumento promedio de unidades por día' : undefined}
          className={avgRoi == null ? '' : avgRoi >= 0 ? 'text-green-700' : 'text-red-700'}
        />
      </section>

      {loading && <EmptyChart>Cargando…</EmptyChart>}
      {report && activities.length === 0 && <EmptyChart>No hay actividades de promoción en este período.</EmptyChart>}

      {report && activities.length > 0 && (
        <>
          <ChartCard
            title="Línea de tiempo"
            subtitle="Inicio y fin de cada actividad, de la más antigua a la más reciente."
            csv={activities.map((a) => ({ actividad: nameOf(a), estado: statusStyle(a.status).label, inicio: a.begin_at, fin: a.end_at }))}
            filename="tiktok-promociones-linea-de-tiempo"
          >
            <PromoGantt activities={activities} selectedId={selectedId} onSelect={toggle} />
          </ChartCard>

          {selected && <PromoDetail activity={selected} onClose={() => setSelectedId(null)} />}

          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="Ranking por ROI"
              subtitle={`Aumento de unidades por día (top ${TOP_RANKING}). Verde sube, rojo baja.`}
              csv={ranking.map((r) => ({ actividad: r.label, aumento_unidades: r.uplift == null ? '' : Number(r.uplift).toFixed(4) }))}
              filename="tiktok-promociones-roi"
            >
              {ranking.length ? (
                <HBarChart
                  rows={ranking}
                  series={[{ key: 'uplift', label: 'Aumento de unidades/día', color: COLOR.good }]}
                  format={axisPct}
                  refLine={0}
                  rowColor={(r) => (Number(r.uplift) < 0 ? COLOR.bad : COLOR.good)}
                  selectedId={selectedId}
                  onRowClick={(r) => toggle(r.id)}
                  labelWidth={170}
                />
              ) : (
                <EmptyChart>Ninguna actividad tiene ROI calculable.</EmptyChart>
              )}
            </ChartCard>

            <ChartCard
              title="Descuento vs aumento de unidades"
              subtitle="Cada burbuja es una actividad; su tamaño son las ventas durante la actividad."
              csv={bubbles.map((b) => ({ actividad: b.label, descuento: b.x.toFixed(4), aumento_unidades: b.y.toFixed(4), ventas_durante: b.z }))}
              filename="tiktok-promociones-descuento-vs-roi"
            >
              <BubbleChart
                points={bubbles}
                xLabel="Descuento"
                yLabel="Aumento de unidades/día"
                zLabel="Ventas durante"
                xFormat={axisPct}
                yFormat={axisPct}
                zFormat={money}
                yRef={0}
                onPointClick={(p) => toggle(p.id)}
              />
            </ChartCard>
          </div>

          <PromotionsTable rows={activities} selectedId={selectedId} onSelect={toggle} />

          <Callout tone="info">
            El ROI es observacional: no separa el efecto de la temporada, la publicidad ni los creadores. Las actividades de nivel tienda no traen productos y se miden contra todas las ventas de TikTok. Se listan las actividades que corrieron en los últimos {days} días o siguen activas.
          </Callout>
        </>
      )}
    </div>
  )
}
