import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Kpi } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import {
  type CreatorOrder,
  commissionByDay,
  commissionTotals,
  tiktokCreatorAccount,
} from '@/lib/tiktokCreatorAccount'
import { useAsync } from '@/lib/useAsync'

import { compareValues, CsvButton, nextSort, PageTitle, SearchBox, SortHead } from '../walmart/walmartUi'
import type { SortState } from '../walmart/walmartSort'
import { shortDate } from './tiktokFormat'
import { NUM_CELL, statusLabel } from './tiktokMoneyFormat'
import { EmptyRows, StatusPill } from './tiktokMoneyUi'

const NO_MATCH = 'Ningún pedido coincide con la búsqueda.'

type SortKey = 'created_at' | 'order_id' | 'status' | 'commission_amount'
const TEXT_KEYS: readonly SortKey[] = ['order_id', 'status']

const sortValue = (o: CreatorOrder, key: SortKey): string | number | null => (key === 'status' ? statusLabel(o.status) : o[key])

const csvRow = (o: CreatorOrder) => ({
  'ID de pedido': o.order_id,
  Fecha: o.created_at ?? '',
  Estado: statusLabel(o.status),
  Comisión: o.commission_amount?.toFixed(2) ?? '',
  Moneda: o.currency ?? '',
})

function EmptyOrders() {
  return (
    <Card>
      <CardContent className="space-y-2 pt-6 text-sm">
        <p className="text-base font-semibold">Todavía no hay pedidos de afiliado</p>
        <p className="text-muted-foreground">
          Un pedido aparece aquí cuando un cliente compra a través de tus enlaces o videos y TikTok lo atribuye a tu cuenta de creador. Todavía
          no hay ventas atribuidas a tus enlaces en este período.
        </p>
        <p className="text-muted-foreground">
          Cuando las haya verás el número de pedidos, la comisión total, lo pendiente y lo ya pagado, una gráfica diaria y el detalle de cada
          pedido con búsqueda, orden y descarga en CSV.
        </p>
      </CardContent>
    </Card>
  )
}

function OrdersTable({ rows, sort, onSort }: { rows: CreatorOrder[]; sort: SortState<SortKey>; onSort: (k: SortKey) => void }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <SortHead sortKey="created_at" label="Fecha" sort={sort} onSort={onSort} right={false} />
          <SortHead sortKey="order_id" label="Pedido" sort={sort} onSort={onSort} right={false} />
          <SortHead sortKey="status" label="Estado" sort={sort} onSort={onSort} right={false} />
          <SortHead sortKey="commission_amount" label="Comisión" sort={sort} onSort={onSort} />
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((o) => (
          <TableRow key={o.order_id}>
            <TableCell className="whitespace-nowrap">{shortDate(o.created_at)}</TableCell>
            <TableCell className="font-mono text-xs">{o.order_id}</TableCell>
            <TableCell>
              <StatusPill status={o.status} />
            </TableCell>
            <TableCell className={NUM_CELL}>{money(o.commission_amount)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/**
 * Pedidos de afiliado de la cuenta de creador propia en el período global: tarjetas de comisión, gráfica diaria y
 * detalle. Con cero pedidos explica por qué y qué se mostrará. Solo lectura.
 */
export function TikTokCreatorOrders() {
  const { preset } = useScope()
  const description = useScopeDescription()
  const days = daysFromPreset(preset)
  const report = useAsync(() => tiktokCreatorAccount.orders(days), [days])
  const data = report.data

  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortState<SortKey>>({ key: 'created_at', dir: -1 })

  const all = useMemo(() => data?.orders ?? [], [data])
  const totals = useMemo(() => commissionTotals(all), [all])
  const daily = useMemo(() => commissionByDay(all), [all])
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return all
      .filter((o) => !q || [o.order_id, statusLabel(o.status)].some((v) => v.toLowerCase().includes(q)))
      .sort((a, b) => compareValues(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir))
  }, [all, search, sort])

  const summary = data?.summary
  const hasOrders = all.length > 0

  return (
    <div className="space-y-6">
      <PageTitle
        title="Pedidos de afiliado (como creador)"
        scope={description}
        note="Ventas atribuidas a tus enlaces y la comisión que generan. Solo lectura."
      />
      <ErrorAlert message={report.error} />
      {data && !data.available && !hasOrders && (
        <Callout tone="info">
          Aún no hay datos de pedidos de afiliado cargados para los últimos {count(data.days)} días. Si ya vendiste algo, aparecerá después de la próxima
          sincronización.
        </Callout>
      )}

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi title="Pedidos" value={summary ? count(summary.orders) : '—'} hint={data ? `últimos ${count(data.days)} días` : undefined} />
        <Kpi title="Comisión total" value={summary ? money(summary.commission) : '—'} hint="suma de las comisiones de todos los pedidos" />
        <Kpi
          title="Pendiente"
          value={data ? money(totals.pending) : '—'}
          hint="comisión aún no pagada (sin cancelados ni devueltos)"
          className={totals.pending > 0 ? 'text-amber-600' : ''}
        />
        <Kpi title="Pagada" value={data ? money(totals.paid) : '—'} hint="comisión que TikTok ya pagó" className={totals.paid > 0 ? 'text-emerald-600' : ''} />
      </div>

      {data && !hasOrders && <EmptyOrders />}

      {hasOrders && (
        <>
          <ChartCard
            title={`Comisión y pedidos por día — ${description}`}
            subtitle="Barras: comisión del día. Línea: pedidos del día."
            filename="tiktok-creator-pedidos-diarios"
            csv={daily.map((d) => ({ Día: d.day, Pedidos: d.orders, Comisión: d.commission.toFixed(2) }))}
          >
            {daily.length === 0 ? (
              <EmptyChart>Los pedidos no traen fecha para graficarlos.</EmptyChart>
            ) : (
              <ComboChart
                data={daily}
                xKey="day"
                height={260}
                xFormat={(v) => shortDate(String(v))}
                rightFormat={count}
                series={[
                  { key: 'commission', label: 'Comisión', type: 'bar', color: COLOR.good, format: money },
                  { key: 'orders', label: 'Pedidos', type: 'line', axis: 'right', color: COLOR.blue, format: count },
                ]}
              />
            )}
          </ChartCard>

          <Card>
            <CardContent className="space-y-3 overflow-x-auto pt-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium">Detalle de pedidos</span>
                <div className="flex items-center gap-2">
                  <SearchBox value={search} onChange={setSearch} placeholder="Buscar por pedido o estado" />
                  <CsvButton filename="tiktok-creator-pedidos.csv" rows={rows.map(csvRow)} />
                </div>
              </div>
              {rows.length > 0 ? (
                <OrdersTable rows={rows} sort={sort} onSort={(k) => setSort((cur) => nextSort(cur, k, TEXT_KEYS))} />
              ) : (
                <EmptyRows loading={report.loading} message={NO_MATCH} />
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
