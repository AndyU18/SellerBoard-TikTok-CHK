import { useMemo, useState } from 'react'

import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { ErrorAlert } from '@/components/analytics/ui'
import { ReturnsCost } from '@/components/analytics/ReturnsCost'
import { ComboChart } from '@/components/charts/ComboChart'
import { HeatTable } from '@/components/charts/HeatTable'
import { axisNumber, ChartCard, COLOR } from '@/components/charts/kit'
import { AsyncBody, HBars, periodLabel } from '@/components/operacion/shared'
import { chartdata } from '@/lib/chartdataOperacion'
import { useScope } from '@/lib/scope'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, type Filters } from '@/lib/api'
import { count, money, percent } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const STATUS_LABEL: Record<string, string> = {
  RETURN_OR_REFUND_REQUEST_COMPLETE: 'Completada',
  RETURN_OR_REFUND_REQUEST_PENDING: 'Pendiente',
  RETURN_OR_REFUND_REQUEST_CANCEL: 'Retirada',
  AWAITING_BUYER_SHIP: 'Esperando al comprador',
  BUYER_SHIPPED_ITEM: 'En camino de vuelta',
  REPLACEMENT_REQUEST_COMPLETE: 'Reemplazo completo',
  complete: 'Completa',
}
const INITIATOR: Record<string, string> = { buyer: 'Comprador', seller: 'Vendedor', system: 'Sistema', operator: 'Plataforma' }

function Kpi({ title, value, hint, className = '' }: { title: string; value: string; hint?: string; className?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-semibold ${className}`}>{value}</div>
        {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
      </CardContent>
    </Card>
  )
}

export function Returns() {
  const scope = useScope()
  const { start, end } = scope.range
  const filters = useMemo<Filters>(
    () => ({ start, end, channel: scope.channelParam, account: scope.account }),
    [start, end, scope.channelParam, scope.account],
  )
  const [period, setPeriod] = useState<string | null>(null)
  const data = useAsync(() => api.returns(filters), [filters])
  const trend = useAsync(() => chartdata.returnsTrend(filters), [filters])
  const matrix = useAsync(() => chartdata.returnsMatrix(filters), [filters])
  const gran = trend.data?.granularity ?? 'month'
  const trendRows = useMemo(() => trend.data?.rows ?? [], [trend.data])
  const inPeriod = (day: string) => {
    if (!period) return true
    if (gran === 'month') return day.startsWith(period)
    const from = Date.parse(`${period}T00:00:00`)
    const t = Date.parse(`${day}T00:00:00`)
    return t >= from && t < from + 7 * 86_400_000
  }
  const sm = data.data?.summary
  const reasons = data.data?.by_reason ?? []
  const returnReasons = reasons.filter((r) => r.kind === 'return')
  const cancelReasons = reasons.filter((r) => r.kind === 'cancellation')
  const products = data.data?.by_product ?? []
  const reasonBars = (rows: typeof reasons) =>
    rows.slice(0, 12).map((r) => ({ id: r.reason, label: r.reason, value: r.cases, extra: `${money(r.refund_total)} reembolsados` }))
  const recent = (data.data?.recent ?? []).filter((c) => inPeriod(c.case_date))

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Devoluciones y cancelaciones (RMA)</h1>
      </div>
      <ChannelOnlyNotice channels={['tiktok_shop', 'walmart']} what="Devoluciones y cancelaciones" />

      <ErrorAlert message={data.error} />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Tasa de devolución"
          value={percent(sm?.return_rate)}
          hint={`${count(sm?.returned_units ?? 0)} de ${count(sm?.units_sold ?? 0)} unidades vendidas`}
        />
        <Kpi
          title="Devoluciones"
          value={count(sm?.returns ?? 0)}
          hint={`${money(sm?.return_refund ?? 0)} reembolsados · ${count(sm?.returns_open ?? 0)} abiertas`}
        />
        <Kpi
          title="Cancelaciones"
          value={count(sm?.cancellations ?? 0)}
          hint={`${money(sm?.cancel_refund ?? 0)} en ventas canceladas`}
        />
        <Kpi
          title="Cancelaciones por el vendedor"
          value={count(sm?.seller_cancellations ?? 0)}
          hint="falta de stock o no se pudo surtir"
          className={(sm?.seller_cancellations ?? 0) > 0 ? 'text-red-600' : ''}
        />
      </div>

      <ChartCard
        title={`Tendencia de devoluciones (por ${gran === 'week' ? 'semana' : 'mes'})`}
        subtitle="Devoluciones en barras y tasa de devolución en línea (eje derecho). Clic en un punto para filtrar los casos recientes."
        csv={trendRows.map((r) => ({
          periodo: r.period,
          devoluciones: r.returns,
          unidades_devueltas: r.returned_units,
          unidades_vendidas: r.units_sold,
          tasa: r.return_rate,
          reembolsado: r.refund,
        }))}
        filename="tendencia-devoluciones"
        actions={
          period && (
            <button type="button" onClick={() => setPeriod(null)} className="text-muted-foreground hover:text-foreground rounded px-2 text-xs hover:bg-slate-100">
              Quitar filtro ({periodLabel(period, gran)})
            </button>
          )
        }
      >
        <AsyncBody state={trend} empty={trendRows.length === 0}>
          <ComboChart
            data={trendRows}
            xKey="period"
            xFormat={(v) => periodLabel(String(v), gran)}
            leftFormat={axisNumber}
            rightFormat={(v) => `${(v * 100).toFixed(1)}%`}
            series={[
              { key: 'returns', label: 'Devoluciones', type: 'bar', color: COLOR.blue, format: count },
              { key: 'returned_units', label: 'Unidades devueltas', type: 'bar', color: '#93c5fd', format: count },
              { key: 'return_rate', label: 'Tasa de devolución', type: 'line', axis: 'right', color: COLOR.bad, format: (v) => percent(v) },
            ]}
            onPointClick={(row) => setPeriod((p) => (p === row.period ? null : String(row.period)))}
          />
        </AsyncBody>
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-2">
        {[
          { title: 'Motivos de devolución', rows: returnReasons },
          { title: 'Motivos de cancelación', rows: cancelReasons },
        ].map((block) => (
          <ChartCard
            key={block.title}
            title={block.title}
            subtitle="Casos por motivo; al pasar el cursor se ve lo reembolsado."
            csv={block.rows.map((r) => ({ motivo: r.reason, casos: r.cases, reembolsado: r.refund_total }))}
            filename={block.title}
          >
            <AsyncBody state={data} empty={block.rows.length === 0}>
              <HBars data={reasonBars(block.rows)} format={count} valueLabel="Casos" />
            </AsyncBody>
          </ChartCard>
        ))}
      </div>

      <ChartCard
        title="Devoluciones por producto y período"
        subtitle="Unidades devueltas de los productos con más devoluciones; el color va de rojo (poco) a verde (mucho) dentro de cada fila."
        csv={(matrix.data?.rows ?? []).map((r) => ({
          sku: r.sku,
          producto: r.product_name,
          total: r.total,
          ...Object.fromEntries((matrix.data?.periods ?? []).map((p, i) => [p, r.values[i]])),
        }))}
        filename="devoluciones-producto-periodo"
      >
        <AsyncBody state={matrix} empty={(matrix.data?.rows.length ?? 0) === 0}>
          <HeatTable
            columns={(matrix.data?.periods ?? []).map((p) => periodLabel(p, matrix.data?.granularity ?? 'month'))}
            rows={(matrix.data?.rows ?? []).map((r) => ({ id: r.sku, label: r.product_name ?? r.sku, sublabel: r.sku, values: r.values }))}
            format={count}
            showChange={false}
          />
        </AsyncBody>
      </ChartCard>

      <Card>
        <CardHeader>
          <CardTitle>Productos con más devoluciones</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Producto</TableHead>
                <TableHead className="text-right">Devueltas</TableHead>
                <TableHead className="text-right">Vendidas</TableHead>
                <TableHead className="text-right">Tasa</TableHead>
                <TableHead className="text-right">Reembolsado</TableHead>
                <TableHead className="text-right">Canceladas</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {products.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground py-8 text-center">
                    {data.loading ? 'Cargando…' : 'Sin datos para este filtro.'}
                  </TableCell>
                </TableRow>
              )}
              {products.map((p) => (
                <TableRow key={p.sku}>
                  <TableCell>
                    <div className="flex min-w-48 flex-col">
                      <span className="font-medium">{p.product_name ?? p.sku}</span>
                      <span className="text-muted-foreground text-xs">{p.sku}</span>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">{count(p.returned_units)}</TableCell>
                  <TableCell className="text-right">{count(p.units_sold)}</TableCell>
                  <TableCell
                    className={`text-right ${(p.return_rate ?? 0) >= 0.1 ? 'font-semibold text-red-600' : ''}`}
                  >
                    {percent(p.return_rate)}
                  </TableCell>
                  <TableCell className="text-right">{money(p.returned_refund)}</TableCell>
                  <TableCell className="text-right">{count(p.cancelled_units)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Casos recientes{period ? ` · ${periodLabel(period, gran)}` : ''}</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Fecha</TableHead>
                <TableHead>Tipo</TableHead>
                <TableHead>Motivo</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>Inició</TableHead>
                <TableHead>Orden</TableHead>
                <TableHead className="text-right">Reembolso</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recent.map((c) => (
                <TableRow key={c.external_id}>
                  <TableCell>{c.case_date}</TableCell>
                  <TableCell>{c.kind === 'return' ? 'Devolución' : 'Cancelación'}</TableCell>
                  <TableCell>{c.reason ?? '—'}</TableCell>
                  <TableCell>{STATUS_LABEL[c.status ?? ''] ?? c.status ?? '—'}</TableCell>
                  <TableCell>{INITIATOR[c.initiated_by ?? ''] ?? '—'}</TableCell>
                  <TableCell className="text-muted-foreground text-xs">{c.external_order_id}</TableCell>
                  <TableCell className="text-right">{money(c.refund_total)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <ReturnsCost />

      <p className="text-muted-foreground max-w-3xl text-xs">
        TikTok Shop informa devoluciones y cancelaciones; Walmart, solo devoluciones. Las solicitudes que el comprador retiró no
        cuentan. La tasa divide las unidades devueltas en el periodo entre las vendidas en el mismo periodo, así que
        puede variar un poco si la devolución llega después.
      </p>
    </div>
  )
}
