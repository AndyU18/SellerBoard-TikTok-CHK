import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, NumberField, pct1 } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { Donut, type DonutSlice } from '@/components/charts/Donut'
import { axisNumber, ChartCard, COLOR, EmptyChart, type CsvRow } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { count } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { tiktokPromos, type PackagesReport, type StuckPackage } from '@/lib/tiktokPromos'
import { useAsync } from '@/lib/useAsync'
import { compareValues } from '@/pages/walmart/walmartSort'
import { CsvButton, nextSort, SearchBox, SortHead, type SortState } from '@/pages/walmart/walmartUi'

import { humanize, NO_VALUE, shortDate, shortDateTime, toMs } from './tiktokFormat'

const DEFAULT_STUCK_DAYS = 5
const MIN_STUCK_DAYS = 1
const MAX_STUCK_DAYS = 60
const FEATURED_STUCK = 5

const STATUS_LABEL: Record<string, string> = { COMPLETED: 'Entregado', CANCELLED: 'Cancelado' }
const statusLabel = (status: string | null) => (status ? (STATUS_LABEL[status] ?? humanize(status)) : NO_VALUE)

type SortKey = 'package' | 'tracking' | 'status' | 'orders' | 'created' | 'movement' | 'days'
const TEXT_KEYS: readonly SortKey[] = ['package', 'tracking', 'status']
const INITIAL_SORT: SortState<SortKey> = { key: 'days', dir: -1 }

const daysText = (v: number | null | undefined) => (v == null ? NO_VALUE : `${v.toFixed(1)} d`)
const trackingOf = (p: StuckPackage) => p.tracking_number ?? 'Sin rastreo'

function sortValue(p: StuckPackage, key: SortKey): string | number | null {
  switch (key) {
    case 'package':
      return p.package_id
    case 'tracking':
      return p.tracking_number
    case 'status':
      return statusLabel(p.status)
    case 'orders':
      return p.order_count
    case 'created':
      return toMs(p.created_at)
    case 'movement':
      return toMs(p.last_movement)
    case 'days':
      return p.days_without_movement
  }
}

function csvRows(rows: StuckPackage[]): CsvRow[] {
  return rows.map((p) => ({
    paquete: p.package_id,
    rastreo: p.tracking_number,
    estado: statusLabel(p.status),
    pedidos: p.order_count,
    creado: p.created_at,
    ultimo_movimiento: p.last_movement,
    ultimo_evento: p.last_event,
    dias_sin_movimiento: p.days_without_movement,
  }))
}

/** Los paquetes que más llevan sin moverse, listos para actuar. */
function FeaturedStuck({ rows, stuckDays }: { rows: StuckPackage[]; stuckDays: number }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Paquetes atascados más urgentes</CardTitle>
        <p className="text-muted-foreground text-xs">No están terminados y llevan más de {stuckDays} días sin movimiento.</p>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">No hay paquetes atascados con este criterio.</p>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {rows.slice(0, FEATURED_STUCK).map((p) => (
              <li key={p.package_id} className="rounded-md border border-red-200 bg-red-50 p-3 text-sm">
                <div className="font-medium text-red-800">{daysText(p.days_without_movement)} sin movimiento</div>
                <div className="truncate" title={trackingOf(p)}>
                  Rastreo: {trackingOf(p)}
                </div>
                <div className="text-muted-foreground text-xs">
                  {statusLabel(p.status)} · {count(p.order_count)} pedido(s) · último evento: {p.last_event ?? NO_VALUE}
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function PackagesTable({ rows }: { rows: StuckPackage[] }) {
  const [sort, setSort] = useState<SortState<SortKey>>(INITIAL_SORT)
  const [search, setSearch] = useState('')
  const onSort = (key: SortKey) => setSort((cur) => nextSort(cur, key, TEXT_KEYS))

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase()
    return rows
      .filter((p) => !q || `${p.package_id} ${p.tracking_number ?? ''}`.toLowerCase().includes(q))
      .sort((a, b) => compareValues(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir))
  }, [rows, search, sort])

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="text-base">Paquetes atascados ({count(shown.length)})</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <SearchBox value={search} onChange={setSearch} placeholder="Buscar rastreo o paquete" />
          <CsvButton filename="tiktok-paquetes-atascados.csv" rows={csvRows(shown)} />
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <SortHead sortKey="package" label="Paquete" sort={sort} onSort={onSort} right={false} />
              <SortHead sortKey="tracking" label="Rastreo" sort={sort} onSort={onSort} right={false} />
              <SortHead sortKey="status" label="Estado" sort={sort} onSort={onSort} right={false} />
              <SortHead sortKey="orders" label="Pedidos" sort={sort} onSort={onSort} />
              <SortHead sortKey="created" label="Creado" sort={sort} onSort={onSort} />
              <SortHead sortKey="movement" label="Último movimiento" sort={sort} onSort={onSort} />
              <SortHead sortKey="days" label="Días sin movimiento" sort={sort} onSort={onSort} />
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((p) => (
              <TableRow key={p.package_id}>
                <TableCell className="font-mono text-xs">{p.package_id}</TableCell>
                <TableCell className="font-mono text-xs">{p.tracking_number ?? NO_VALUE}</TableCell>
                <TableCell>{statusLabel(p.status)}</TableCell>
                <TableCell className="text-right">{count(p.order_count)}</TableCell>
                <TableCell className="text-right whitespace-nowrap">{shortDate(p.created_at)}</TableCell>
                <TableCell className="text-right whitespace-nowrap">{shortDateTime(p.last_movement)}</TableCell>
                <TableCell className="text-right font-medium text-red-700">{daysText(p.days_without_movement)}</TableCell>
              </TableRow>
            ))}
            {shown.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="text-muted-foreground text-center">
                  Ningún paquete coincide con la búsqueda.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

const toSlices = (counts: Record<string, number>): DonutSlice[] => Object.entries(counts).map(([id, value]) => ({ id, label: statusLabel(id), value }))

/** Una serie de barras apiladas por estado presente en el período. */
const daySeries = (report: PackagesReport): ComboSeries[] =>
  Object.keys(report.summary.by_status).map((status) => ({
    key: status,
    label: statusLabel(status),
    type: 'bar',
    stack: 'status',
    color: status === 'COMPLETED' ? COLOR.good : status === 'CANCELLED' ? COLOR.neutral : undefined,
  }))

/** Paquetes de TikTok Shop: entrega, tiempos y los que llevan días sin moverse. */
export function TikTokPackages() {
  const { preset } = useScope()
  const description = useScopeDescription()
  const days = daysFromPreset(preset)
  const [stuckDays, setStuckDays] = useState(DEFAULT_STUCK_DAYS)
  const data = useAsync(() => tiktokPromos.packages(days, stuckDays), [days, stuckDays])
  const report = data.data

  const summary = report?.summary
  const statusSlices = useMemo(() => (report ? toSlices(report.summary.by_status) : []), [report])
  const series = useMemo(() => (report ? daySeries(report) : []), [report])
  const stuck = useMemo(() => report?.stuck ?? [], [report])

  const loading = data.loading && !report
  const placeholder = loading ? 'Cargando…' : NO_VALUE

  return (
    <div className="space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Paquetes de TikTok Shop</h1>
        <p className="text-muted-foreground text-sm">{description}</p>
        <Hint>Paquetes creados en el período. Solo se muestran identificadores de paquete y rastreo, sin datos personales.</Hint>
      </header>

      <ErrorAlert message={data.error} />

      <section aria-label="Indicadores" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Paquetes" value={summary ? count(summary.packages) : placeholder} />
        <Kpi title="Tasa de entrega" value={summary ? pct1(summary.delivery_rate) : placeholder} hint={summary ? 'Entregados / no cancelados' : undefined} />
        <Kpi title="Días promedio de entrega" value={summary ? daysText(summary.avg_delivery_days) : placeholder} />
        <Kpi
          title="Atascados"
          value={summary ? count(summary.stuck) : placeholder}
          hint={summary ? `Más de ${summary.stuck_days} días sin movimiento` : undefined}
          className={summary && summary.stuck > 0 ? 'text-red-700' : ''}
        />
      </section>

      {summary && <Callout tone="info">{summary.delivered_at_note}</Callout>}

      <div className="flex flex-wrap items-end gap-3">
        <NumberField label="Días sin movimiento para marcar atascado" value={stuckDays} min={MIN_STUCK_DAYS} max={MAX_STUCK_DAYS} suffix="días" onCommit={setStuckDays} />
        {data.loading && report && <span className="text-muted-foreground pb-2 text-xs">Actualizando…</span>}
      </div>

      {loading && <EmptyChart>Cargando…</EmptyChart>}
      {report && !report.available && <EmptyChart>No hay paquetes en este período.</EmptyChart>}

      {report && report.available && (
        <>
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="Paquetes por estado"
              csv={statusSlices.map((s) => ({ estado: s.label, paquetes: s.value }))}
              filename="tiktok-paquetes-por-estado"
            >
              <Donut slices={statusSlices} format={count} centerLabel="paquetes" />
            </ChartCard>

            <ChartCard
              title="Tiempos de entrega"
              subtitle="Paquetes entregados según los días que tardaron desde su creación."
              csv={report.delivery_distribution.map((d) => ({ tiempo: d.bucket, paquetes: d.count }))}
              filename="tiktok-paquetes-tiempos-de-entrega"
            >
              <ComboChart
                data={report.delivery_distribution}
                xKey="bucket"
                series={[{ key: 'count', label: 'Paquetes', type: 'bar', color: COLOR.blue }]}
                leftFormat={axisNumber}
                height={240}
              />
            </ChartCard>
          </div>

          <ChartCard
            title="Paquetes por día"
            subtitle="Paquetes creados cada día, por estado actual."
            csv={report.by_day.map((d) => ({ ...d }))}
            filename="tiktok-paquetes-por-dia"
          >
            <ComboChart data={report.by_day} xKey="date" series={series} leftFormat={axisNumber} xFormat={(v) => shortDate(String(v))} />
          </ChartCard>

          <FeaturedStuck rows={stuck} stuckDays={summary?.stuck_days ?? stuckDays} />
          <PackagesTable rows={stuck} />
        </>
      )}
    </div>
  )
}
