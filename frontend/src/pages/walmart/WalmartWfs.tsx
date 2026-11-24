import { useMemo, useState } from 'react'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, days1, ErrorAlert, Hint, Kpi, PILL, Pill, Segmented } from '@/components/analytics/ui'
import { Donut, type DonutSlice } from '@/components/charts/Donut'
import { ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHeader, TableRow, TableHead } from '@/components/ui/table'
import { count } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'
import { walmartQuality, type WfsRow, type WfsStatus } from '@/lib/walmartQuality'

import { compareValues, nextSort, type SortState } from './walmartSort'
import { CsvButton, PageTitle, SearchBox, SortHead } from './walmartUi'

type SortKey = 'sku' | 'avail_to_sell' | 'on_hand' | 'in_transit_or_reserved' | 'daily_units' | 'cover_days' | 'status' | 'seller_fulfilled_stock'
type StatusFilter = WfsStatus | 'all'

const TEXT_KEYS: readonly SortKey[] = ['sku', 'status']
/** Cuántos SKU caben en la gráfica de cobertura (los de menor cobertura primero). */
const TOP_COVER_BARS = 15

const STATUS: Record<WfsStatus, { label: string; color: string; pill: string }> = {
  out: { label: 'Agotado', color: COLOR.bad, pill: PILL.red },
  low: { label: 'Poco stock', color: COLOR.amber, pill: PILL.amber },
  ok: { label: 'Con stock', color: COLOR.good, pill: PILL.green },
  no_sales: { label: 'Sin ventas', color: COLOR.neutral, pill: PILL.slate },
  empty_no_sales: { label: 'Vacío sin ventas', color: '#cbd5e1', pill: PILL.slate },
}
const STATUS_ORDER: WfsStatus[] = ['out', 'low', 'ok', 'no_sales', 'empty_no_sales']

const sum = (rows: WfsRow[], pick: (r: WfsRow) => number) => rows.reduce((a, r) => a + pick(r), 0)

/** Cobertura por SKU sumando sus nodos; solo SKU con ventas (sin ventas no hay cobertura). */
function coverageBySku(rows: WfsRow[]): HBarRow[] {
  const bySku = new Map<string, { avail: number; daily: number; status: WfsStatus }>()
  for (const r of rows) {
    const cur = bySku.get(r.sku)
    bySku.set(r.sku, {
      avail: (cur?.avail ?? 0) + r.avail_to_sell,
      daily: (cur?.daily ?? 0) + r.daily_units,
      // el peor estado entre nodos manda
      status: cur && STATUS_ORDER.indexOf(cur.status) < STATUS_ORDER.indexOf(r.status) ? cur.status : r.status,
    })
  }
  return [...bySku.entries()]
    .filter(([, v]) => v.daily > 0)
    .map(([sku, v]) => ({ id: sku, label: sku, cover: Math.max(v.avail, 0) / v.daily, status: v.status }))
    .sort((a, b) => Number(a.cover) - Number(b.cover))
    .slice(0, TOP_COVER_BARS)
}

export function WalmartWfs() {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const scope = useScopeDescription()
  const state = useAsync(() => walmartQuality.wfsInventory(days), [days])
  const d = state.data
  const s = d?.summary
  const all = useMemo(() => d?.rows ?? [], [d?.rows])

  const [sort, setSort] = useState<SortState<SortKey>>({ key: 'status', dir: 1 })
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const empty = (msg: string) => <EmptyChart>{state.loading ? 'Cargando…' : msg}</EmptyChart>

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const list = all.filter((r) => (statusFilter === 'all' || r.status === statusFilter) && (!needle || r.sku.toLowerCase().includes(needle)))
    const order = (r: WfsRow) => STATUS_ORDER.indexOf(r.status)
    return list.sort((a, b) =>
      sort.key === 'status' ? (order(a) - order(b)) * sort.dir || compareValues(a.cover_days, b.cover_days, 1) : compareValues(a[sort.key], b[sort.key], sort.dir),
    )
  }, [all, search, statusFilter, sort])

  const coverRows = useMemo(() => coverageBySku(all), [all])
  const statusSlices = useMemo<DonutSlice[]>(
    () => STATUS_ORDER.map((k) => ({ id: k, label: STATUS[k].label, value: all.filter((r) => r.status === k).length })),
    [all],
  )
  const statusCounts = useMemo(() => new Map(statusSlices.map((x) => [x.id, x.value])), [statusSlices])
  const noSalesWithStock = statusCounts.get('no_sales') ?? 0

  const statusOptions: { value: StatusFilter; label: string; count?: number }[] = [
    { value: 'all', label: 'Todos', count: all.length },
    ...STATUS_ORDER.filter((k) => (statusCounts.get(k) ?? 0) > 0).map((k) => ({ value: k, label: STATUS[k].label, count: statusCounts.get(k) })),
  ]

  const csvRows = rows.map((r) => ({
    SKU: r.sku,
    Nodo: r.node,
    Disponible: r.avail_to_sell,
    'En mano': r.on_hand,
    'En tránsito/reservado': r.in_transit_or_reserved,
    'Ventas diarias': r.daily_units,
    'Cobertura (días)': r.cover_days,
    Estado: STATUS[r.status].label,
    'Stock del vendedor': r.seller_fulfilled_stock,
    'En otro inventario': r.stocked_elsewhere ? 'sí' : 'no',
  }))
  const onSort = (key: SortKey) => setSort((cur) => nextSort(cur, key, TEXT_KEYS))

  return (
    <div className="space-y-6">
      <PageTitle
        title="Inventario de WFS (Fulfillment)"
        scope={scope}
        note={d ? `Cobertura con las ventas de ${d.days} días; poco stock = menos de ${d.low_cover_days} días${s?.snapshot_at ? ` · foto ${s.snapshot_at.slice(0, 16).replace('T', ' ')}` : ''}` : undefined}
      />

      <ErrorAlert message={state.error} />
      {state.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Kpi title="SKU en WFS" value={count(s?.skus ?? 0)} hint="con existencias reportadas por Walmart" />
        <Kpi title="Unidades disponibles" value={count(s?.avail_to_sell_units ?? 0)} hint="listas para vender" />
        <Kpi
          title="Agotados"
          value={count(s?.out_of_stock ?? 0)}
          hint={`${count(s?.out_of_stock_total ?? 0)} en total, contando los que no venden`}
          className={(s?.out_of_stock ?? 0) > 0 ? 'text-red-600' : ''}
        />
        <Kpi title="Con poco stock" value={count(s?.low_stock ?? 0)} hint={`menos de ${d?.low_cover_days ?? '—'} días de cobertura`} className={(s?.low_stock ?? 0) > 0 ? 'text-amber-600' : ''} />
        <Kpi title="En tránsito / reservadas" value={count(sum(all, (r) => r.in_transit_or_reserved))} hint="unidades en mano que aún no se pueden vender" />
      </div>

      {(s?.stocked_elsewhere ?? 0) > 0 && (
        <Callout tone="warning">
          {count(s?.stocked_elsewhere)} SKU están agotados en WFS pero tienen stock del vendedor: considera enviarlos a WFS o venderlos con envío propio.
        </Callout>
      )}
      {noSalesWithStock > 0 && (
        <Callout tone="info">
          {count(noSalesWithStock)} SKU tienen unidades en WFS pero no registran ventas en el período: su cobertura no se puede calcular y pueden estar inmovilizando capital.
        </Callout>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Días de cobertura por SKU"
          subtitle={`Los ${TOP_COVER_BARS} SKU con menos cobertura (solo con ventas). Un clic filtra la tabla. Línea: umbral de ${d?.low_cover_days ?? '—'} días.`}
          filename="walmart-wfs-cobertura"
          csv={coverRows.map((r) => ({ SKU: r.label, 'Cobertura (días)': Math.round(Number(r.cover) * 10) / 10, Estado: STATUS[r.status as WfsStatus].label }))}
        >
          {coverRows.length === 0 ? (
            empty('Ningún SKU con ventas para calcular cobertura.')
          ) : (
            <HBarChart
              rows={coverRows}
              series={[{ key: 'cover', label: 'Días de cobertura' }]}
              format={days1}
              refLine={d?.low_cover_days}
              labelWidth={120}
              rowColor={(row) => STATUS[row.status as WfsStatus].color}
              selectedId={search ? coverRows.find((r) => r.id === search)?.id : null}
              onRowClick={(row) => setSearch(row.id)}
            />
          )}
        </ChartCard>

        <ChartCard
          title="Estado de los SKU"
          subtitle="Cantidad de SKU por estado. Un clic en una porción filtra la tabla."
          filename="walmart-wfs-estado"
          csv={statusSlices.map((x) => ({ Estado: x.label, SKU: x.value }))}
        >
          {all.length === 0 ? empty('Sin existencias WFS.') : <Donut slices={statusSlices} format={count} centerLabel="SKU" onSliceClick={(x) => setStatusFilter(x.id as WfsStatus)} />}
        </ChartCard>
      </div>

      <Card>
        <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <CardTitle>Detalle por SKU</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <SearchBox value={search} onChange={setSearch} placeholder="Buscar SKU" />
            <CsvButton filename="walmart-wfs-inventario.csv" rows={csvRows} />
          </div>
        </CardHeader>
        <CardContent className="space-y-3 overflow-x-auto">
          <Segmented value={statusFilter} options={statusOptions} onChange={setStatusFilter} label="Filtrar por estado" />
          <Table>
            <TableHeader>
              <TableRow>
                <SortHead sortKey="sku" label="SKU" sort={sort} onSort={onSort} right={false} />
                <SortHead sortKey="avail_to_sell" label="Disponible" sort={sort} onSort={onSort} />
                <SortHead sortKey="on_hand" label="En mano" sort={sort} onSort={onSort} />
                <SortHead sortKey="in_transit_or_reserved" label="En tránsito" sort={sort} onSort={onSort} />
                <SortHead sortKey="daily_units" label="Ventas/día" sort={sort} onSort={onSort} />
                <SortHead sortKey="cover_days" label="Cobertura" sort={sort} onSort={onSort} />
                <SortHead sortKey="status" label="Estado" sort={sort} onSort={onSort} right={false} />
                <SortHead sortKey="seller_fulfilled_stock" label="Stock del vendedor" sort={sort} onSort={onSort} />
                <TableHead>Otro inventario</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} className="text-muted-foreground py-8 text-center">
                    {state.loading ? 'Cargando…' : 'Ningún SKU coincide con los filtros.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r) => (
                <TableRow key={`${r.sku}|${r.node ?? ''}`} className={r.status === 'out' ? 'bg-red-50' : ''}>
                  <TableCell>
                    <span className="font-mono text-xs">{r.sku}</span>
                    {r.node && <div className="text-muted-foreground text-xs">Nodo {r.node}</div>}
                  </TableCell>
                  <TableCell className="text-right font-medium">{count(r.avail_to_sell)}</TableCell>
                  <TableCell className="text-right">{count(r.on_hand)}</TableCell>
                  <TableCell className="text-right">{count(r.in_transit_or_reserved)}</TableCell>
                  <TableCell className="text-right">{r.daily_units.toFixed(2)}</TableCell>
                  <TableCell className="text-right">{r.cover_days == null ? '—' : days1(r.cover_days)}</TableCell>
                  <TableCell>
                    <Pill label={STATUS[r.status].label} className={STATUS[r.status].pill} />
                  </TableCell>
                  <TableCell className="text-right">{r.seller_fulfilled_stock == null ? '—' : count(r.seller_fulfilled_stock)}</TableCell>
                  <TableCell>{r.stocked_elsewhere ? <Pill label="Stock fuera de WFS" className={PILL.orange} /> : <span className="text-muted-foreground">—</span>}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Hint>
        Cobertura = unidades disponibles / ventas diarias de Walmart en el período. En tránsito / reservadas = unidades en mano que no
        están disponibles para vender. “Stock fuera de WFS” marca SKU sin disponibilidad en WFS que sí tienen existencias del vendedor.
      </Hint>
    </div>
  )
}
