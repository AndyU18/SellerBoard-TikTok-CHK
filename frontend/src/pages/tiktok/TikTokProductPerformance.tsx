import { useMemo, useState } from 'react'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, ErrorAlert, Hint, Kpi, pct1, Pill, PILL } from '@/components/analytics/ui'
import { type BubblePoint, BubbleChart } from '@/components/charts/BubbleChart'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { Donut, type DonutSlice } from '@/components/charts/Donut'
import { axisMoney, axisNumber, ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { type GmvSource, type ProductPerformanceRow, tiktokCatalog } from '@/lib/tiktokCatalog'
import { useAsync } from '@/lib/useAsync'

import { PageTitle, SearchBox, CsvButton, SortHead, compareValues, nextSort, type SortState } from '../walmart/walmartUi'
import { clip, marginClass, matchesSearch } from './tiktokCatalogFormat'
import { Fact, ProductImage, SidePanel } from './tiktokCatalogUi'
import { humanize, NO_VALUE } from './tiktokFormat'

type SortKey =
  | 'title'
  | 'gmv'
  | 'orders'
  | 'units_sold'
  | 'impressions'
  | 'page_views'
  | 'click_through_rate'
  | 'conversion_rate'
  | 'profit'
  | 'margin'

const TEXT_KEYS: readonly SortKey[] = ['title']
/** Productos en el ranking de barras (los de mayor GMV). */
const TOP_BARS = 15
const LABEL_MAX = 26
const BUBBLE_GROUP_REST = 'Resto de productos'
const BUBBLE_GROUP_FLAGGED = 'Mucho tráfico y poca conversión'
/** Mismo criterio del backend: conversión bajo la mitad de la de la tienda cuenta como baja. */
const LOW_CONVERSION_RATIO = 0.5

const SOURCE_LABEL: Record<GmvSource, string> = { live: 'LIVE', video: 'VIDEO', product_card: 'Tarjeta de producto' }
const SOURCES: GmvSource[] = ['live', 'video', 'product_card']

const rate = (v: number | null | undefined) => pct1(v)
const shortDate = (v: string | number) => String(v).slice(5)

function ProductPanel({ product, trend, onClose }: { product: ProductPerformanceRow; trend: { date: string; gmv: number; orders: number; units_sold: number }[]; onClose: () => void }) {
  const slices = SOURCES.map<DonutSlice>((k) => ({ id: k, label: SOURCE_LABEL[k], value: product.gmv_by_source[k] ?? 0 }))
  const hasSources = slices.some((s) => s.value > 0)
  const series: ComboSeries[] = [{ key: 'gmv', label: 'GMV de la tienda', type: 'area', color: COLOR.blue, format: money }]
  return (
    <SidePanel title={product.title ?? product.product_id} subtitle={`ID ${product.product_id}`} onClose={onClose}>
      <div className="flex items-center gap-3">
        <ProductImage src={product.thumb_url} title={product.title} className="size-24" />
        <div className="space-y-1">
          {product.status && <Pill label={humanize(product.status)} className={PILL.slate} />}
          {product.high_traffic_low_conversion && <Pill label={BUBBLE_GROUP_FLAGGED} className={PILL.amber} />}
          {!product.linked_to_master && <p className="text-muted-foreground text-xs">Sin SKU maestro: no hay utilidad real.</p>}
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <Fact label="GMV" value={money(product.gmv)} />
        <Fact label="Órdenes" value={count(product.orders)} />
        <Fact label="Unidades" value={count(product.units_sold)} />
        <Fact label="Visitas a la página" value={count(product.page_views)} />
        <Fact label="CTR" value={rate(product.click_through_rate)} />
        <Fact label="Conversión" value={rate(product.conversion_rate)} />
        <Fact label="Utilidad" value={money(product.profit)} className={product.profit != null && product.profit < 0 ? 'text-red-600' : ''} />
        <Fact label="Margen" value={rate(product.margin)} className={marginClass(product.margin)} />
      </dl>
      <section>
        <h3 className="mb-2 text-sm font-medium">GMV por origen</h3>
        {hasSources ? <Donut slices={slices} format={money} height={180} centerLabel="GMV" /> : <EmptyChart>TikTok no desglosa el GMV de este producto por origen.</EmptyChart>}
      </section>
      <section>
        <h3 className="mb-2 text-sm font-medium">Tendencia diaria del GMV (toda la tienda)</h3>
        <ComboChart data={trend} xKey="date" series={series} xFormat={shortDate} leftFormat={axisMoney} height={180} />
      </section>
      <Hint>La utilidad es lo ya liquidado vía el SKU maestro; el GMV es la demanda. Pueden no coincidir.</Hint>
    </SidePanel>
  )
}

function OpportunityList({ rows, onOpen }: { rows: ProductPerformanceRow[]; onOpen: (p: ProductPerformanceRow) => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Oportunidades: mucho tráfico y poca conversión</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {rows.length === 0 ? (
          <EmptyChart>Ningún producto combina tráfico alto con conversión baja.</EmptyChart>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Producto</TableHead>
                <TableHead className="text-right">Visitas</TableHead>
                <TableHead className="text-right">CTR</TableHead>
                <TableHead className="text-right">Conversión</TableHead>
                <TableHead className="text-right">GMV</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.product_id}>
                  <TableCell>
                    <button type="button" onClick={() => onOpen(r)} className="flex items-center gap-2 text-left hover:underline focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
                      <ProductImage src={r.thumb_url} title={r.title} className="size-8" />
                      <span>{clip(r.title ?? r.product_id, 60)}</span>
                    </button>
                  </TableCell>
                  <TableCell className="text-right">{count(r.page_views)}</TableCell>
                  <TableCell className="text-right">{rate(r.click_through_rate)}</TableCell>
                  <TableCell className="text-right font-medium text-amber-700">{rate(r.conversion_rate)}</TableCell>
                  <TableCell className="text-right">{money(r.gmv)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

export function TikTokProductPerformance() {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const scope = useScopeDescription()
  const state = useAsync(() => tiktokCatalog.productPerformance(days), [days])
  const d = state.data
  const s = d?.summary ?? null
  const products = useMemo(() => d?.products ?? [], [d?.products])

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortState<SortKey>>({ key: 'gmv', dir: -1 })
  const selected = useMemo(() => products.find((p) => p.product_id === selectedId) ?? null, [products, selectedId])
  const open = (p: ProductPerformanceRow) => setSelectedId(p.product_id)
  const openById = (x: { id: string }) => setSelectedId(x.id)

  const avgCtr = useMemo(() => {
    const values = products.map((p) => p.click_through_rate).filter((v): v is number => v != null)
    return values.length ? values.reduce((a, v) => a + v, 0) / values.length : null
  }, [products])

  const barRows = useMemo<HBarRow[]>(
    () => products.slice(0, TOP_BARS).map((p) => ({ id: p.product_id, label: clip(p.title ?? p.product_id, LABEL_MAX), gmv: p.gmv })),
    [products],
  )

  const bubbles = useMemo<BubblePoint[]>(() => {
    const pts = products
      .filter((p) => p.page_views != null && p.conversion_rate != null)
      .map<BubblePoint>((p) => ({
        id: p.product_id,
        label: p.title ?? p.product_id,
        x: p.page_views ?? 0,
        y: p.conversion_rate ?? 0,
        z: p.gmv,
        group: p.high_traffic_low_conversion ? BUBBLE_GROUP_FLAGGED : BUBBLE_GROUP_REST,
      }))
    // El primer grupo toma el azul y el segundo el rojo: el resto va primero para que lo resaltado salga en rojo.
    return pts.sort((a, b) => Number(a.group === BUBBLE_GROUP_FLAGGED) - Number(b.group === BUBBLE_GROUP_FLAGGED))
  }, [products])
  const lowConversionLine = s?.store_conversion_rate == null ? undefined : s.store_conversion_rate * LOW_CONVERSION_RATIO

  const rows = useMemo(() => {
    const list = products.filter((p) => matchesSearch(search, p.title, p.product_id))
    return list.sort((a, b) => compareValues(a[sort.key], b[sort.key], sort.dir))
  }, [products, search, sort])
  const onSort = (key: SortKey) => setSort((cur) => nextSort(cur, key, TEXT_KEYS))

  const csvRows = rows.map((r) => ({
    Producto: r.title,
    ID: r.product_id,
    GMV: r.gmv,
    Órdenes: r.orders,
    Unidades: r.units_sold,
    Impresiones: r.impressions,
    Visitas: r.page_views,
    CTR: r.click_through_rate,
    Conversión: r.conversion_rate,
    'GMV LIVE': r.gmv_by_source.live,
    'GMV VIDEO': r.gmv_by_source.video,
    'GMV tarjeta': r.gmv_by_source.product_card,
    Utilidad: r.profit,
    Margen: r.margin,
  }))

  return (
    <div className="space-y-6">
      <PageTitle
        title="Rendimiento por producto"
        scope={scope}
        note={d?.window ? `Foto del ${d.window.snapshot_date} (ventana ${d.window.start} a ${d.window.end}); la tendencia cubre ${days} días` : undefined}
      />

      <ErrorAlert message={state.error} />
      {state.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}
      {d && !d.available && <Callout tone="info">Todavía no hay datos de rendimiento por producto de TikTok Shop. Aparecerán tras la próxima sincronización.</Callout>}

      {s && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <Kpi title="GMV" value={money(s.gmv)} hint={`${count(s.products)} productos`} />
            <Kpi title="Órdenes" value={count(s.orders)} />
            <Kpi title="Unidades" value={count(s.units_sold)} />
            <Kpi title="CTR promedio" value={rate(avgCtr)} hint="promedio simple entre productos" />
            <Kpi title="Conversión de la tienda" value={rate(s.store_conversion_rate)} hint="órdenes / visitantes" />
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="Ranking por GMV"
              subtitle={`Los ${TOP_BARS} productos con más GMV. Un clic abre su detalle.`}
              filename="tiktok-productos-gmv"
              csv={products.slice(0, TOP_BARS).map((p) => ({ Producto: p.title, GMV: p.gmv }))}
            >
              <HBarChart rows={barRows} series={[{ key: 'gmv', label: 'GMV' }]} format={axisMoney} labelWidth={170} selectedId={selectedId} onRowClick={openById} />
            </ChartCard>

            <ChartCard
              title="Tráfico frente a conversión"
              subtitle={`Tamaño = GMV. En rojo, los de tráfico alto (desde ${count(s.high_traffic_views_threshold)} visitas) y conversión bajo la mitad de la tienda; línea horizontal = ese corte.`}
              filename="tiktok-productos-trafico-conversion"
              csv={bubbles.map((b) => ({ Producto: b.label, Visitas: b.x, Conversión: b.y, GMV: b.z, Grupo: b.group }))}
            >
              <BubbleChart
                points={bubbles}
                xLabel="Visitas a la página"
                yLabel="Conversión"
                zLabel="GMV"
                xFormat={axisNumber}
                yFormat={rate}
                zFormat={money}
                xRef={s.high_traffic_views_threshold}
                yRef={lowConversionLine}
                onPointClick={openById}
              />
            </ChartCard>
          </div>

          <OpportunityList rows={d?.traffic_low_conversion ?? []} onOpen={open} />

          <Card>
            <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <CardTitle>Todos los productos</CardTitle>
              <div className="flex flex-wrap items-center gap-2">
                <SearchBox value={search} onChange={setSearch} placeholder="Buscar producto" />
                <CsvButton filename="tiktok-rendimiento-productos.csv" rows={csvRows} />
              </div>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortHead sortKey="title" label="Producto" sort={sort} onSort={onSort} right={false} />
                    <SortHead sortKey="gmv" label="GMV" sort={sort} onSort={onSort} />
                    <SortHead sortKey="orders" label="Órdenes" sort={sort} onSort={onSort} />
                    <SortHead sortKey="units_sold" label="Unidades" sort={sort} onSort={onSort} />
                    <SortHead sortKey="impressions" label="Impresiones" sort={sort} onSort={onSort} />
                    <SortHead sortKey="page_views" label="Visitas" sort={sort} onSort={onSort} />
                    <SortHead sortKey="click_through_rate" label="CTR" sort={sort} onSort={onSort} />
                    <SortHead sortKey="conversion_rate" label="Conversión" sort={sort} onSort={onSort} />
                    <SortHead sortKey="profit" label="Utilidad" sort={sort} onSort={onSort} />
                    <SortHead sortKey="margin" label="Margen" sort={sort} onSort={onSort} />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={10} className="text-muted-foreground py-8 text-center">
                        Ningún producto coincide con la búsqueda.
                      </TableCell>
                    </TableRow>
                  )}
                  {rows.map((r) => (
                    <TableRow key={r.product_id}>
                      <TableCell>
                        <button type="button" onClick={() => open(r)} className="flex items-center gap-2 text-left hover:underline focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
                          <ProductImage src={r.thumb_url} title={r.title} className="size-8" />
                          <span>{clip(r.title ?? r.product_id, 50)}</span>
                        </button>
                      </TableCell>
                      <TableCell className="text-right font-medium">{money(r.gmv)}</TableCell>
                      <TableCell className="text-right">{count(r.orders)}</TableCell>
                      <TableCell className="text-right">{count(r.units_sold)}</TableCell>
                      <TableCell className="text-right">{count(r.impressions)}</TableCell>
                      <TableCell className="text-right">{count(r.page_views)}</TableCell>
                      <TableCell className="text-right">{rate(r.click_through_rate)}</TableCell>
                      <TableCell className="text-right">{rate(r.conversion_rate)}</TableCell>
                      <TableCell className={`text-right ${r.profit != null && r.profit < 0 ? 'text-red-600' : ''}`}>{r.profit == null ? NO_VALUE : money(r.profit)}</TableCell>
                      <TableCell className={`text-right ${marginClass(r.margin)}`}>{rate(r.margin)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <Hint>
            GMV, órdenes y tráfico vienen de la última foto de TikTok; la utilidad es lo ya liquidado, unido por SKU maestro. {count(s.with_traffic_detail)} de{' '}
            {count(s.products)} productos traen detalle de tráfico.
          </Hint>
        </>
      )}

      {selected && <ProductPanel product={selected} trend={d?.trend ?? []} onClose={() => setSelectedId(null)} />}
    </div>
  )
}
