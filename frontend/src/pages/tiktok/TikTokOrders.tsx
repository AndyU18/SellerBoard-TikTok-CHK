// Pedidos de TikTok Shop: tarjetas, gráficas de utilidad y tabla paginada (mismo motor que Pedidos de Walmart, con el canal del contexto).
import { X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { ErrorAlert, Hint, Kpi, pct1, Segmented } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { Donut } from '@/components/charts/Donut'
import { axisMoney, axisNumber, ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { OrderProfitTable } from '@/components/orders/OrderProfitTable'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { api, type Filters } from '@/lib/api'
import { count, money } from '@/lib/format'
import { type OrderDriverKey, orderProfit, type OrderProfitFilter, type OrderProfitRow, type OrderSort } from '@/lib/orderProfit'
import { binLimits } from '@/lib/orderProfitBins'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

import { CsvButton, PageTitle, SearchBox } from '@/pages/walmart/walmartUi'

const PAGE = 25
const SEARCH_DELAY_MS = 300
/** El resumen y las gráficas vienen aparte de las filas: basta pedir una. */
const OVERVIEW_ROWS = 1
const NO_ORDERS = 'No hay órdenes de TikTok Shop en este período.'
const SORT_OPTIONS: { value: OrderSort; label: string }[] = [
  { value: 'profit_asc', label: 'Peor utilidad' },
  { value: 'profit_desc', label: 'Mejor utilidad' },
  { value: 'date_desc', label: 'Más recientes' },
  { value: 'sales_desc', label: 'Mayor venta' },
]

const csvRow = (r: OrderProfitRow) => ({
  Fecha: r.date,
  Orden: r.order,
  Estado: r.state ?? '',
  Unidades: r.units,
  Ventas: r.net_sales.toFixed(2),
  Comisiones: r.fees.toFixed(2),
  Envío: r.shipping.toFixed(2),
  Reembolsos: r.refunds.toFixed(2),
  COGS: r.cogs.toFixed(2),
  Utilidad: r.profit.toFixed(2),
  Margen: r.margin == null ? '' : `${(r.margin * 100).toFixed(1)}%`,
  Causa: r.driver_short ?? '',
})

export function TikTokOrders() {
  const { range, channelParam, account } = useScope()
  const description = useScopeDescription()
  const start = range.start ?? ''
  const end = range.end ?? ''

  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [onlyLoss, setOnlyLoss] = useState(false)
  const [sort, setSort] = useState<OrderSort>('profit_asc')
  const [driver, setDriver] = useState<OrderDriverKey | ''>('')
  const [bin, setBin] = useState('')

  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), SEARCH_DELAY_MS)
    return () => clearTimeout(t)
  }, [text])

  const filterKey = [start, end, channelParam, account, q, onlyLoss, sort, driver, bin].join('|')
  const [pageState, setPageState] = useState({ key: filterKey, page: 0 })
  const page = pageState.key === filterKey ? pageState.page : 0
  const setPage = (v: number) => setPageState({ key: filterKey, page: v })

  // Tarjetas y gráficas: todo el período y canal, sin búsqueda ni facetas (así no se vacían al filtrar la tabla).
  const scopeFilter = useMemo<OrderProfitFilter>(
    () => ({ start, end, channel: channelParam, account }),
    [start, end, channelParam, account],
  )
  const apiFilter = useMemo<Filters>(() => ({ start, end, channel: channelParam, account }), [start, end, channelParam, account])
  const overview = useAsync(() => orderProfit.list({ ...scopeFilter, limit: OVERVIEW_ROWS }), [scopeFilter])
  const daily = useAsync(() => api.timeseries('day', apiFilter), [apiFilter])
  const returns = useAsync(() => api.returns(apiFilter), [apiFilter])

  const limits = binLimits(bin)
  const tableFilter = useMemo<OrderProfitFilter>(
    () => ({
      ...scopeFilter,
      q,
      only_loss: onlyLoss,
      driver: driver || undefined,
      profit_min: limits?.min ?? undefined,
      profit_max: limits?.max ?? undefined,
    }),
    [scopeFilter, q, onlyLoss, driver, limits?.min, limits?.max],
  )
  const table = useAsync(() => orderProfit.list({ ...tableFilter, sort, limit: PAGE, offset: page * PAGE }), [tableFilter, sort, page])

  const ov = overview.data
  const sm = ov?.summary
  const netSales = (ov?.by_channel ?? []).reduce((a, c) => a + c.net_sales, 0)
  const avgTicket = sm && sm.orders ? netSales / sm.orders : null
  const rows = table.data?.rows ?? []
  const total = table.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE))

  const histData = (ov?.histogram ?? []).map((b) => {
    const isLoss = b.max != null && b.max <= 0
    return { key: b.key, label: b.label, loss: isLoss ? b.orders : 0, gain: isLoss ? 0 : b.orders }
  })
  const slices = (ov?.by_driver ?? []).map((x) => ({ id: x.key, label: x.label, value: x.amount }))
  const dailyData = (daily.data?.rows ?? []).map((r) => ({ day: r.period ?? '', units: r.units, profit: r.net_profit }))

  const reset = () => {
    setText('')
    setQ('')
    setOnlyLoss(false)
    setDriver('')
    setBin('')
  }
  const hasFilter = !!(q || text || onlyLoss || driver || bin)
  const waitingOverview = (msg: string) => <EmptyChart>{overview.loading ? 'Cargando…' : msg}</EmptyChart>

  return (
    <div className="space-y-6">
      <PageTitle
        title="Pedidos"
        scope={description}
        note="La utilidad es ventas − comisiones − envío − reembolsos − costo del producto, antes de publicidad."
      />
      <ErrorAlert message={overview.error ?? table.error ?? daily.error ?? returns.error} />

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-6">
        <Kpi title="Órdenes" value={sm ? count(sm.orders) : '—'} />
        <Kpi title="Ventas netas" value={ov ? money(netSales) : '—'} />
        <Kpi
          title="Utilidad"
          value={sm ? money(sm.profit_total) : '—'}
          className={sm ? (sm.profit_total >= 0 ? 'text-emerald-600' : 'text-red-600') : ''}
        />
        <Kpi
          title="Con pérdida"
          value={sm ? pct1(sm.loss_pct) : '—'}
          hint={sm ? `${count(sm.loss_orders)} órdenes` : undefined}
          className={sm && (sm.loss_pct ?? 0) > 0 ? 'text-red-600' : ''}
        />
        <Kpi title="Ticket promedio" value={money(avgTicket)} hint="ventas netas por orden" />
        <Kpi
          title="Cancelaciones"
          value={returns.data ? count(returns.data.summary.cancellations) : '—'}
          hint={returns.data ? `${count(returns.data.summary.seller_cancellations)} del vendedor` : undefined}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title={`Unidades y utilidad por día — ${description}`}
          subtitle="Barras: unidades vendidas. Línea (eje derecho): utilidad neta del día."
          filename="tiktok-pedidos-diario"
          csv={dailyData.map((d) => ({ Día: d.day, Unidades: d.units, Utilidad: d.profit }))}
        >
          {dailyData.length === 0 ? (
            <EmptyChart>{daily.loading ? 'Cargando…' : NO_ORDERS}</EmptyChart>
          ) : (
            <ComboChart
              data={dailyData}
              xKey="day"
              height={240}
              leftFormat={axisNumber}
              rightFormat={axisMoney}
              series={[
                { key: 'units', label: 'Unidades', type: 'bar', color: COLOR.blue, format: count },
                { key: 'profit', label: 'Utilidad', type: 'line', axis: 'right', color: COLOR.good, format: money },
              ]}
            />
          )}
        </ChartCard>
        <ChartCard
          title={`Utilidad por orden — ${description}`}
          subtitle="Cuántas órdenes caen en cada rango de utilidad. Clic en una barra para filtrar la tabla."
          filename="tiktok-utilidad-histograma"
          csv={(ov?.histogram ?? []).map((b) => ({ Rango: b.label, Órdenes: b.orders }))}
        >
          {histData.length === 0 || sm?.orders === 0 ? (
            waitingOverview(NO_ORDERS)
          ) : (
            <ComboChart
              data={histData}
              xKey="label"
              height={240}
              leftFormat={axisNumber}
              onPointClick={(row) => setBin(bin === row.key ? '' : String(row.key))}
              series={[
                { key: 'loss', label: 'Con pérdida', type: 'bar', color: COLOR.bad, stack: 'a', format: count },
                { key: 'gain', label: 'Con ganancia', type: 'bar', color: COLOR.good, stack: 'a', format: count },
              ]}
            />
          )}
        </ChartCard>
        <ChartCard
          title={`Pérdida por causa principal — ${description}`}
          subtitle="Monto perdido según el costo que más pesó en cada orden con pérdida. Clic para filtrar la tabla."
          filename="tiktok-perdida-causas"
          csv={(ov?.by_driver ?? []).map((x) => ({ Causa: x.label, Órdenes: x.orders, Monto: x.amount }))}
        >
          {slices.length === 0 ? (
            waitingOverview('No hay órdenes con pérdida en este período.')
          ) : (
            <Donut
              slices={slices}
              format={money}
              centerLabel="perdido"
              onSliceClick={(sl) => setDriver(driver === sl.id ? '' : (sl.id as OrderDriverKey))}
            />
          )}
        </ChartCard>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <SearchBox value={text} onChange={setText} placeholder="Buscar orden o SKU" />
          <Segmented label="Orden de la tabla" value={sort} options={SORT_OPTIONS} onChange={setSort} />
          <label className="flex h-9 items-center gap-2 text-sm">
            <input type="checkbox" className="size-4" checked={onlyLoss} onChange={(e) => setOnlyLoss(e.target.checked)} />
            Solo con pérdida
          </label>
          {driver && (
            <button type="button" onClick={() => setDriver('')} className="flex h-9 items-center gap-1 rounded-full border px-3 text-xs">
              Causa: {slices.find((s) => s.id === driver)?.label ?? driver} <X className="size-3" />
            </button>
          )}
          {bin && (
            <button type="button" onClick={() => setBin('')} className="flex h-9 items-center gap-1 rounded-full border px-3 text-xs">
              Rango: {ov?.histogram.find((b) => b.key === bin)?.label ?? bin} <X className="size-3" />
            </button>
          )}
          {hasFilter && (
            <Button variant="outline" onClick={reset}>
              Limpiar
            </Button>
          )}
          <div className="sm:ml-auto">
            <CsvButton filename={`tiktok-pedidos-pagina-${page + 1}.csv`} rows={rows.map(csvRow)} />
          </div>
        </CardContent>
      </Card>

      <div className="text-sm">
        <strong>{count(total)}</strong> órdenes en la tabla
      </div>
      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <OrderProfitTable rows={rows} loading={table.loading} emptyText="No hay órdenes con estos filtros." />
        </CardContent>
      </Card>
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <span className="text-muted-foreground">
          Página {page + 1} de {pages}
        </span>
        <div className="flex gap-2">
          <Button variant="outline" disabled={page === 0 || table.loading} onClick={() => setPage(page - 1)}>
            Anterior
          </Button>
          <Button variant="outline" disabled={page + 1 >= pages || table.loading} onClick={() => setPage(page + 1)}>
            Siguiente
          </Button>
        </div>
      </div>
      <Hint>El CSV incluye solo las {PAGE} órdenes de la página que estás viendo.</Hint>
    </div>
  )
}
