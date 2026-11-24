import { useMemo, useState } from 'react'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, ErrorAlert, Hint, Kpi, pct1 } from '@/components/analytics/ui'
import { type BubblePoint, BubbleChart } from '@/components/charts/BubbleChart'
import { axisMoney, axisNumber, ChartCard } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { tiktokCatalog } from '@/lib/tiktokCatalog'
import { useAsync } from '@/lib/useAsync'

import { compareValues, CsvButton, nextSort, PageTitle, SearchBox, SortHead, type SortState } from '../walmart/walmartUi'
import { clip, MARGIN_GOOD, marginClass, matchesSearch } from './tiktokCatalogFormat'
import { NO_VALUE } from './tiktokFormat'

type SortKey = 'seller_sku' | 'title' | 'master_sku' | 'gmv' | 'orders' | 'units_sold' | 'profit' | 'profit_per_unit' | 'margin'

const TEXT_KEYS: readonly SortKey[] = ['seller_sku', 'title', 'master_sku']
/** SKU en las barras (los de mayor GMV). */
const TOP_BARS = 15
const LABEL_MAX = 24
/** El primer grupo toma el azul y el segundo el rojo de la paleta del kit. */
const GROUP_HEALTHY = `Margen desde ${MARGIN_GOOD * 100}%`
const GROUP_LOW = 'Margen bajo o negativo'

const SERIES = [
  { key: 'gmv', label: 'GMV' },
  { key: 'profit', label: 'Utilidad' },
]
const skuName = (r: { seller_sku: string | null; sku_id: string }) => r.seller_sku ?? r.sku_id

export function TikTokSkuPerformance() {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const scope = useScopeDescription()
  const state = useAsync(() => tiktokCatalog.skuPerformance(days), [days])
  const d = state.data
  const s = d?.summary ?? null
  const skus = useMemo(() => d?.skus ?? [], [d?.skus])

  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortState<SortKey>>({ key: 'gmv', dir: -1 })

  const barRows = useMemo<HBarRow[]>(
    () => skus.slice(0, TOP_BARS).map((r) => ({ id: r.sku_id, label: clip(skuName(r), LABEL_MAX), gmv: r.gmv, profit: r.profit })),
    [skus],
  )

  const bubbles = useMemo<BubblePoint[]>(
    () =>
      skus
        .filter((r) => r.margin != null)
        .map<BubblePoint>((r) => ({
          id: r.sku_id,
          label: skuName(r),
          x: r.units_sold,
          y: r.margin ?? 0,
          z: r.gmv,
          group: (r.margin ?? 0) >= MARGIN_GOOD ? GROUP_HEALTHY : GROUP_LOW,
        }))
        .sort((a, b) => Number(a.group === GROUP_LOW) - Number(b.group === GROUP_LOW)),
    [skus],
  )

  const rows = useMemo(() => {
    const list = skus.filter((r) => matchesSearch(search, r.seller_sku, r.sku_id, r.title, r.master_sku, r.master_name))
    return list.sort((a, b) => compareValues(a[sort.key], b[sort.key], sort.dir))
  }, [skus, search, sort])
  const onSort = (key: SortKey) => setSort((cur) => nextSort(cur, key, TEXT_KEYS))

  const csvRows = rows.map((r) => ({
    SKU: r.seller_sku,
    'ID de SKU': r.sku_id,
    Producto: r.title,
    'SKU maestro': r.master_sku,
    Precio: r.price,
    GMV: r.gmv,
    Órdenes: r.orders,
    Unidades: r.units_sold,
    Utilidad: r.profit,
    'Utilidad por unidad': r.profit_per_unit,
    Margen: r.margin,
  }))

  return (
    <div className="space-y-6">
      <PageTitle
        title="Rendimiento por SKU"
        scope={scope}
        note={d?.window ? `Foto del ${d.window.snapshot_date} (ventana ${d.window.start} a ${d.window.end})` : undefined}
      />

      <ErrorAlert message={state.error} />
      {state.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}
      {d && !d.available && <Callout tone="info">Todavía no hay datos de rendimiento por SKU de TikTok Shop. Aparecerán tras la próxima sincronización.</Callout>}

      {s && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi title="SKU con ventas" value={count(s.skus)} />
            <Kpi title="GMV" value={money(s.gmv)} />
            <Kpi title="Unidades" value={count(s.units_sold)} />
            <Kpi title="Sin SKU maestro" value={count(s.unlinked)} hint="sin utilidad real calculable" className={s.unlinked > 0 ? 'text-amber-600' : ''} />
          </div>

          {s.unlinked > 0 && (
            <Callout tone="warning">
              {count(s.unlinked)} SKU de TikTok no están vinculados a un SKU maestro, por eso no muestran utilidad ni margen. Vincúlalos con un alias de SKU para ver su rentabilidad real.
            </Callout>
          )}

          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="GMV y utilidad por SKU"
              subtitle={`Los ${TOP_BARS} SKU con más GMV. La utilidad es lo ya liquidado; sin SKU maestro no hay barra.`}
              filename="tiktok-sku-gmv-utilidad"
              csv={skus.slice(0, TOP_BARS).map((r) => ({ SKU: skuName(r), GMV: r.gmv, Utilidad: r.profit }))}
            >
              <HBarChart rows={barRows} series={SERIES} format={axisMoney} labelWidth={150} />
            </ChartCard>

            <ChartCard
              title="Unidades frente a margen"
              subtitle="Tamaño = GMV. Rojo: margen bajo o negativo. Solo SKU con utilidad real."
              filename="tiktok-sku-unidades-margen"
              csv={bubbles.map((b) => ({ SKU: b.label, Unidades: b.x, Margen: b.y, GMV: b.z }))}
            >
              <BubbleChart points={bubbles} xLabel="Unidades vendidas" yLabel="Margen" zLabel="GMV" xFormat={axisNumber} yFormat={pct1} zFormat={money} yRef={0} />
            </ChartCard>
          </div>

          <Card>
            <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <CardTitle>Detalle por SKU</CardTitle>
              <div className="flex flex-wrap items-center gap-2">
                <SearchBox value={search} onChange={setSearch} placeholder="Buscar SKU o producto" />
                <CsvButton filename="tiktok-rendimiento-sku.csv" rows={csvRows} />
              </div>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortHead sortKey="seller_sku" label="SKU" sort={sort} onSort={onSort} right={false} />
                    <SortHead sortKey="title" label="Producto" sort={sort} onSort={onSort} right={false} />
                    <SortHead sortKey="master_sku" label="SKU maestro" sort={sort} onSort={onSort} right={false} />
                    <SortHead sortKey="gmv" label="GMV" sort={sort} onSort={onSort} />
                    <SortHead sortKey="orders" label="Órdenes" sort={sort} onSort={onSort} />
                    <SortHead sortKey="units_sold" label="Unidades" sort={sort} onSort={onSort} />
                    <SortHead sortKey="profit" label="Utilidad" sort={sort} onSort={onSort} />
                    <SortHead sortKey="profit_per_unit" label="Utilidad/unidad" sort={sort} onSort={onSort} />
                    <SortHead sortKey="margin" label="Margen" sort={sort} onSort={onSort} />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={9} className="text-muted-foreground py-8 text-center">
                        Ningún SKU coincide con la búsqueda.
                      </TableCell>
                    </TableRow>
                  )}
                  {rows.map((r) => (
                    <TableRow key={r.sku_id} className={r.linked_to_master ? '' : 'bg-amber-50'}>
                      <TableCell className="font-mono text-xs">{skuName(r)}</TableCell>
                      <TableCell>{clip(r.title, 40)}</TableCell>
                      <TableCell>
                        {r.master_sku ? (
                          <>
                            <span className="font-mono text-xs">{r.master_sku}</span>
                            {r.master_name && <div className="text-muted-foreground text-xs">{clip(r.master_name, 30)}</div>}
                          </>
                        ) : (
                          <span className="text-amber-700 text-xs">Sin vincular</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right font-medium">{money(r.gmv)}</TableCell>
                      <TableCell className="text-right">{count(r.orders)}</TableCell>
                      <TableCell className="text-right">{count(r.units_sold)}</TableCell>
                      <TableCell className={`text-right ${r.profit != null && r.profit < 0 ? 'text-red-600' : ''}`}>{r.profit == null ? NO_VALUE : money(r.profit)}</TableCell>
                      <TableCell className="text-right">{r.profit_per_unit == null ? NO_VALUE : money(r.profit_per_unit)}</TableCell>
                      <TableCell className={`text-right ${marginClass(r.margin)}`}>{pct1(r.margin)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <Hint>El GMV es la demanda de TikTok; la utilidad, utilidad por unidad y margen salen de lo ya liquidado del SKU maestro vinculado.</Hint>
        </>
      )}
    </div>
  )
}
