import { ArrowDown, ArrowUp, ArrowUpDown, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { ErrorAlert, Hint, pct1 } from '@/components/analytics/ui'
import { BubbleChart, type BubblePoint } from '@/components/charts/BubbleChart'
import { ComboChart } from '@/components/charts/ComboChart'
import { Donut } from '@/components/charts/Donut'
import { axisNumber, ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { CHANNEL_LABEL, type Channel } from '@/lib/api'
import { count, money } from '@/lib/format'
import {
  orderProfit,
  type OrderDriverKey,
  type OrderProfitFilter,
  type OrderProfitRow,
  type OrderSort,
} from '@/lib/orderProfit'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const PAGE = 25
const BUBBLE_ROWS = 200

const DRIVER_STYLE: Record<OrderDriverKey, string> = {
  cogs: 'bg-amber-100 text-amber-800',
  fees: 'bg-violet-100 text-violet-800',
  shipping: 'bg-blue-100 text-blue-800',
  refunds: 'bg-red-100 text-red-800',
  promo: 'bg-pink-100 text-pink-800',
}

const channelName = (c: string) => CHANNEL_LABEL[c as Channel] ?? c

function csvCell(v: string | number | null | undefined): string {
  const s = v == null ? '' : String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

function Kpi({ title, value, tone = 'plain', hint }: { title: string; value: string; tone?: 'good' | 'bad' | 'plain'; hint?: string }) {
  const color = tone === 'good' ? 'text-emerald-600' : tone === 'bad' ? 'text-red-600' : 'text-slate-900 dark:text-slate-100'
  return (
    <div className="rounded-lg border bg-white p-3 shadow-xs dark:bg-slate-900">
      <div className="text-muted-foreground text-[11px] font-semibold tracking-wide uppercase">{title}</div>
      <div className={`mt-1 text-xl font-bold tabular-nums ${color}`}>{value}</div>
      {hint && <div className="text-muted-foreground mt-0.5 text-[11px]">{hint}</div>}
    </div>
  )
}

function SortHead({
  label,
  asc,
  desc,
  sort,
  onSort,
  right = true,
}: {
  label: string
  asc: OrderSort
  desc: OrderSort
  sort: OrderSort
  onSort: (s: OrderSort) => void
  right?: boolean
}) {
  const Icon = sort === asc ? ArrowUp : sort === desc ? ArrowDown : ArrowUpDown
  return (
    <TableHead className={right ? 'text-right' : undefined} aria-sort={sort === asc ? 'ascending' : sort === desc ? 'descending' : 'none'}>
      <button
        type="button"
        onClick={() => onSort(sort === desc ? asc : desc)}
        className="inline-flex items-center gap-1 font-medium hover:underline"
      >
        {label}
        <Icon className={`size-3 ${sort === asc || sort === desc ? '' : 'opacity-40'}`} />
      </button>
    </TableHead>
  )
}

export function OrderProfit() {
  const scope = useScope()
  const description = useScopeDescription()
  const { range, channelParam, account } = scope
  const start = range.start ?? ''
  const end = range.end ?? ''

  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [onlyLoss, setOnlyLoss] = useState(false)
  const [sort, setSort] = useState<OrderSort>('profit_asc')
  const [driver, setDriver] = useState<OrderDriverKey | ''>('')
  const [bin, setBin] = useState('')

  // El buscador espera 300 ms después de la última tecla antes de consultar.
  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 300)
    return () => clearTimeout(t)
  }, [text])

  const filterKey = [start, end, channelParam, account, q, onlyLoss, sort, driver, bin].join('|')
  const [pageState, setPageState] = useState({ key: filterKey, page: 0 })
  const page = pageState.key === filterKey ? pageState.page : 0
  const setPage = (v: number) => setPageState({ key: filterKey, page: v })

  const limits = binRange(bin)
  const base = useMemo<OrderProfitFilter>(
    () => ({
      start,
      end,
      channel: channelParam,
      account,
      q,
      only_loss: onlyLoss,
      driver: driver || undefined,
      profit_min: limits?.min ?? undefined,
      profit_max: limits?.max ?? undefined,
    }),
    [start, end, channelParam, account, q, onlyLoss, driver, limits?.min, limits?.max],
  )
  const table = useAsync(
    () => orderProfit.list({ ...base, sort, limit: PAGE, offset: page * PAGE }),
    [base, sort, page],
  )
  // Muestra para la burbuja: las órdenes con peor utilidad del mismo filtro.
  const worst = useAsync(() => orderProfit.list({ ...base, sort: 'profit_asc', limit: BUBBLE_ROWS }), [base])

  const d = table.data
  const sm = d?.summary
  const rows = d?.rows ?? []
  const total = d?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE))

  const histData = (d?.histogram ?? []).map((b) => {
    const isLoss = b.max != null && b.max <= 0
    return { key: b.key, label: b.label, loss: isLoss ? b.orders : 0, gain: isLoss ? 0 : b.orders }
  })
  const slices = (d?.by_driver ?? []).map((x) => ({ id: x.key, label: x.label, value: x.amount }))
  const channelData = (d?.by_channel ?? []).map((c) => ({
    label: channelName(c.channel),
    loss: c.profit < 0 ? c.profit : 0,
    gain: c.profit >= 0 ? c.profit : 0,
    profit: c.profit,
    orders: c.orders,
  }))
  const points: BubblePoint[] = (worst.data?.rows ?? []).map((r) => ({
    id: `${r.channel}:${r.order}`,
    label: `Orden ${r.order}`,
    x: r.net_sales,
    y: r.profit,
    z: Math.max(1, r.units),
    group: channelName(r.channel),
  }))

  const reset = () => {
    setText('')
    setQ('')
    setOnlyLoss(false)
    setDriver('')
    setBin('')
  }
  const hasFilter = !!(q || text || onlyLoss || driver || bin)
  const binLabel = d?.histogram.find((b) => b.key === bin)?.label

  const download = () => {
    const header = ['Fecha', 'Orden', 'Canal', 'Tienda', 'Estado', 'Unidades', 'Ventas brutas', 'Promo', 'Comisiones', 'Envío', 'Reembolsos', 'COGS', 'Utilidad', 'Margen', 'Causa principal']
    const line = (r: OrderProfitRow) => [
      r.date, r.order, channelName(r.channel), r.account, r.state, r.units,
      r.gross_sales.toFixed(2), r.promo.toFixed(2), r.fees.toFixed(2), r.shipping.toFixed(2),
      r.refunds.toFixed(2), r.cogs.toFixed(2), r.profit.toFixed(2),
      r.margin == null ? '' : (r.margin * 100).toFixed(1) + '%', r.driver_label,
    ]
    const body = [header, ...rows.map(line)].map((l) => l.map(csvCell).join(',')).join('\r\n')
    const url = URL.createObjectURL(new Blob(['﻿' + body], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `utilidad-por-orden-pagina-${page + 1}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const empty = (msg: string) => <EmptyChart>{table.loading ? 'Cargando…' : msg}</EmptyChart>
  const none = 'No hay órdenes con estos filtros.'

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Utilidad por orden</h1>
      <Hint>
        La publicidad no se reparte por orden: aquí la utilidad es ventas − promo − comisiones − envío − reembolsos −
        costo del producto, antes de publicidad. Por eso no cuadra con la ganancia neta del tablero.
      </Hint>
      <ErrorAlert message={table.error ?? worst.error} />

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi title="Órdenes" value={sm ? count(sm.orders) : '—'} hint={sm ? `${count(sm.loss_orders)} con pérdida` : undefined} />
        <Kpi title="Con pérdida" value={sm ? pct1(sm.loss_pct) : '—'} tone={sm && (sm.loss_pct ?? 0) > 0 ? 'bad' : 'plain'} hint="de todas las órdenes del filtro" />
        <Kpi title="Pérdida total" value={sm ? money(-sm.loss_total) : '—'} tone={sm && sm.loss_total > 0 ? 'bad' : 'plain'} hint="suma de las órdenes con utilidad negativa" />
        <Kpi
          title="Utilidad prom. por orden"
          value={sm ? money(sm.avg_profit) : '—'}
          tone={sm && sm.avg_profit != null ? (sm.avg_profit >= 0 ? 'good' : 'bad') : 'plain'}
          hint={sm ? `Total ${money(sm.profit_total)}` : undefined}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title={`Utilidad por orden — ${description}`}
          subtitle="Cuántas órdenes caen en cada rango de utilidad. Clic en una barra para filtrar la tabla."
          filename="utilidad-por-orden-histograma"
          csv={(d?.histogram ?? []).map((b) => ({ Rango: b.label, Órdenes: b.orders }))}
        >
          {histData.length === 0 || sm?.orders === 0 ? (
            empty(none)
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
          filename="utilidad-por-orden-causas"
          csv={(d?.by_driver ?? []).map((x) => ({ Causa: x.label, Órdenes: x.orders, Monto: x.amount }))}
        >
          {slices.length === 0 ? (
            empty('No hay órdenes con pérdida en este filtro.')
          ) : (
            <Donut
              slices={slices}
              format={money}
              centerLabel="perdido"
              onSliceClick={(sl) => setDriver(driver === sl.id ? '' : (sl.id as OrderDriverKey))}
            />
          )}
        </ChartCard>
        <ChartCard
          title={`Utilidad por canal — ${description}`}
          subtitle="Suma de la utilidad de las órdenes de cada canal (antes de publicidad)."
          filename="utilidad-por-orden-canales"
          csv={(d?.by_channel ?? []).map((c) => ({ Canal: channelName(c.channel), Órdenes: c.orders, 'Con pérdida': c.loss_orders, Utilidad: c.profit }))}
        >
          {channelData.length === 0 ? (
            empty(none)
          ) : (
            <ComboChart
              data={channelData}
              xKey="label"
              height={240}
              series={[
                { key: 'loss', label: 'Pérdida', type: 'bar', color: COLOR.bad, stack: 'a', format: money },
                { key: 'gain', label: 'Ganancia', type: 'bar', color: COLOR.good, stack: 'a', format: money },
              ]}
            />
          )}
        </ChartCard>
        <ChartCard
          title={`Ventas vs utilidad — ${description}`}
          subtitle={`Las ${BUBBLE_ROWS} órdenes con peor utilidad del filtro; el tamaño es el número de unidades. Clic para buscar la orden.`}
          filename="utilidad-por-orden-dispersion"
          csv={(worst.data?.rows ?? []).map((r) => ({ Orden: r.order, 'Ventas netas': r.net_sales, Utilidad: r.profit }))}
        >
          {points.length === 0 ? (
            <EmptyChart>{worst.loading ? 'Cargando…' : none}</EmptyChart>
          ) : (
            <BubbleChart
              points={points}
              xLabel="Ventas netas"
              yLabel="Utilidad"
              zLabel="Unidades"
              xFormat={money}
              yFormat={money}
              zFormat={count}
              yRef={0}
              height={260}
              onPointClick={(p) => setText(p.label.replace('Orden ', ''))}
            />
          )}
        </ChartCard>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Orden o SKU</span>
            <Input className="h-9 w-56" value={text} placeholder="Ej. 5774… o SKU" onChange={(e) => setText(e.target.value)} />
          </label>
          <label className="flex h-9 items-center gap-2 text-sm">
            <input type="checkbox" className="size-4" checked={onlyLoss} onChange={(e) => setOnlyLoss(e.target.checked)} />
            Solo con pérdida
          </label>
          {hasFilter && (
            <Button variant="outline" onClick={reset}>
              Limpiar
            </Button>
          )}
          {driver && (
            <button type="button" onClick={() => setDriver('')} className="flex h-9 items-center gap-1 rounded-full border px-3 text-xs">
              Causa: {slices.find((s) => s.id === driver)?.label ?? driver} <X className="size-3" />
            </button>
          )}
          {bin && (
            <button type="button" onClick={() => setBin('')} className="flex h-9 items-center gap-1 rounded-full border px-3 text-xs">
              Rango: {binLabel ?? bin} <X className="size-3" />
            </button>
          )}
          <Button onClick={download} disabled={rows.length === 0} className="sm:ml-auto">
            Descargar la página (CSV)
          </Button>
        </CardContent>
      </Card>

      <div className="text-sm">
        <strong>{count(total)}</strong> órdenes en la tabla
      </div>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <SortHead label="Fecha" asc="date_asc" desc="date_desc" sort={sort} onSort={setSort} right={false} />
                <TableHead>Orden</TableHead>
                <TableHead>Canal · tienda</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead className="text-right">Un.</TableHead>
                <SortHead label="Ventas" asc="sales_asc" desc="sales_desc" sort={sort} onSort={setSort} />
                <TableHead className="text-right">Promo</TableHead>
                <TableHead className="text-right">Comisiones</TableHead>
                <TableHead className="text-right">Envío</TableHead>
                <TableHead className="text-right">Reembolsos</TableHead>
                <TableHead className="text-right">COGS</TableHead>
                <SortHead label="Utilidad" asc="profit_asc" desc="profit_desc" sort={sort} onSort={setSort} />
                <TableHead className="text-right">Margen</TableHead>
                <TableHead>Causa principal</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={14} className="text-muted-foreground py-10 text-center">
                    {table.loading ? 'Cargando…' : none}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r) => (
                <TableRow key={`${r.channel}:${r.account}:${r.order}`}>
                  <TableCell className="whitespace-nowrap">{r.date}</TableCell>
                  <TableCell className="font-mono text-xs">{r.order}</TableCell>
                  <TableCell className="whitespace-nowrap">
                    {channelName(r.channel)} <span className="text-muted-foreground text-xs">· {r.account}</span>
                  </TableCell>
                  <TableCell>{r.state ?? '—'}</TableCell>
                  <TableCell className="text-right tabular-nums">{r.units}</TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums">{money(r.net_sales)}</TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums">{money(r.promo)}</TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums">{money(r.fees)}</TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums" title={r.label_cost ? `Incluye etiqueta Redo ${money(r.label_cost)}` : undefined}>
                    {money(r.shipping)}
                  </TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums">{money(r.refunds)}</TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums">{money(r.cogs)}</TableCell>
                  <TableCell className={`text-right font-semibold whitespace-nowrap tabular-nums ${r.profit < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                    {money(r.profit)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{pct1(r.margin)}</TableCell>
                  <TableCell>
                    {r.driver ? (
                      <span
                        title={r.driver_label ?? undefined}
                        className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${DRIVER_STYLE[r.driver]}`}
                      >
                        {r.driver_short}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
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

      <Hint>
        Envío incluye la etiqueta real de Redo cuando existe; el costo del producto usa el costo vigente a la fecha de
        la orden. El CSV incluye solo las {PAGE} órdenes de la página que estás viendo.
      </Hint>
    </div>
  )
}

/** Límites de utilidad de un rango del histograma (claves fijas del backend). */
function binRange(key: string): { min: number | null; max: number | null } | null {
  const ranges: Record<string, { min: number | null; max: number | null }> = {
    lt_m10: { min: null, max: -10 },
    m10_0: { min: -10, max: 0 },
    '0_5': { min: 0, max: 5 },
    '5_10': { min: 5, max: 10 },
    '10_20': { min: 10, max: 20 },
    gt_20: { min: 20, max: null },
  }
  return ranges[key] ?? null
}
