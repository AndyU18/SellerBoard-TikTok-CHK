import { Callout, ErrorAlert, Hint, Kpi, pct1 } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { Donut } from '@/components/charts/Donut'
import { axisPct, ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count, money } from '@/lib/format'
import { rangeDays } from '@/lib/periodDays'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'
import { type PaymentsFigures, type PaymentsResponse, walmartPayments } from '@/lib/walmartPayments'

import { CsvButton, PageTitle } from './walmartUi'

const num = 'text-right whitespace-nowrap tabular-nums'
const NO_SALES = 'No hay ventas de Walmart en este período.'

type MonthFigures = PaymentsFigures & { month: string }

/** "2026-11" -> "nov 2026". */
function monthLabel(month: string): string {
  const [year, mm] = month.split('-').map(Number)
  return new Date(year, mm - 1, 1).toLocaleDateString('es', { month: 'short', year: 'numeric' })
}

const csvRow = (m: MonthFigures) => ({
  Mes: m.month,
  Órdenes: m.orders,
  Ventas: m.sales.toFixed(2),
  'Comisión real': m.fees_actual.toFixed(2),
  'Comisión estimada': m.fees_estimated.toFixed(2),
  'Comisión total': m.fees.toFixed(2),
  '% efectivo': m.effective_pct == null ? '' : (m.effective_pct * 100).toFixed(2),
  'Órdenes con comisión estimada': m.estimated_orders,
  Reembolsos: m.refunds.toFixed(2),
  'Etiquetas Walmart': m.shipping_labels.toFixed(2),
  'Neto esperado': m.net_expected.toFixed(2),
})

function EstimateNotice({ data }: { data: PaymentsResponse }) {
  const k = data.kpis
  if (!k.estimated_orders) return null
  return (
    <Callout tone="warning">
      <strong>{pct1(k.estimated_fees_pct)}</strong> de las comisiones ({money(k.fees_estimated)}) sigue siendo
      estimada: {count(k.estimated_orders)} de {count(k.orders)} órdenes aún no aparecen en un reporte de
      conciliación de Walmart y se calcularon con {pct1(data.assumed_commission_rate)} de la venta. Se reemplazan
      por la comisión real cuando Walmart las incluye en su reporte.
    </Callout>
  )
}

function MonthTable({ months }: { months: MonthFigures[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Mes</TableHead>
          <TableHead className="text-right">Órdenes</TableHead>
          <TableHead className="text-right">Ventas</TableHead>
          <TableHead className="text-right">Comisión real</TableHead>
          <TableHead className="text-right">Comisión estimada</TableHead>
          <TableHead className="text-right">% efectivo</TableHead>
          <TableHead className="text-right">Sin conciliar</TableHead>
          <TableHead className="text-right">Reembolsos</TableHead>
          <TableHead className="text-right">Etiquetas</TableHead>
          <TableHead className="text-right">Neto esperado</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {months.map((m) => (
          <TableRow key={m.month}>
            <TableCell className="whitespace-nowrap">{monthLabel(m.month)}</TableCell>
            <TableCell className="text-right tabular-nums">{count(m.orders)}</TableCell>
            <TableCell className={num}>{money(m.sales)}</TableCell>
            <TableCell className={num}>{money(m.fees_actual)}</TableCell>
            <TableCell className={num}>{money(m.fees_estimated)}</TableCell>
            <TableCell className="text-right tabular-nums">{pct1(m.effective_pct)}</TableCell>
            <TableCell className="text-right tabular-nums">{count(m.estimated_orders)}</TableCell>
            <TableCell className={num}>{money(m.refunds)}</TableCell>
            <TableCell className={num}>{money(m.shipping_labels)}</TableCell>
            <TableCell className={`${num} font-semibold`}>{money(m.net_expected)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function WalmartPayments() {
  const { range } = useScope()
  const description = useScopeDescription()
  const days = rangeDays(range)
  const report = useAsync(() => walmartPayments.report(days), [days])
  const data = report.data
  const k = data?.kpis

  const monthRows = data?.by_month ?? []
  const chartMonths = monthRows.map((m) => ({ ...m, label: monthLabel(m.month), pct: m.effective_pct }))
  const slices = (data?.by_adjustment ?? []).map((a) => ({ id: a.key, label: a.label, value: a.amount }))
  const empty = (msg: string) => <EmptyChart>{report.loading ? 'Cargando…' : msg}</EmptyChart>

  return (
    <div className="space-y-6">
      <PageTitle title="Pagos y comisiones" scope={description} />
      <ErrorAlert message={report.error} />
      {data && <EstimateNotice data={data} />}

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi title="Comisión total" value={k ? money(k.fees) : '—'} hint={k ? `sobre ${money(k.sales)} de ventas` : undefined} />
        <Kpi title="% efectivo de comisión" value={k ? pct1(k.effective_pct) : '—'} hint="comisión total / ventas" />
        <Kpi
          title="Comisión estimada (sin conciliar)"
          value={k ? money(k.fees_estimated) : '—'}
          hint={k ? `${count(k.estimated_orders)} órdenes · ${pct1(k.estimated_fees_pct)} de la comisión` : undefined}
          className={k && k.estimated_orders ? 'text-amber-600' : ''}
        />
        <Kpi
          title="Ajustes"
          value={k ? money(k.adjustments) : '—'}
          hint={k ? `Reembolsos ${money(k.refunds)} · Etiquetas ${money(k.shipping_labels)}` : undefined}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title={`Ventas y comisión por mes — ${description}`}
          subtitle="Barras: ventas y comisión total. Línea (eje derecho): % efectivo de comisión."
          filename="walmart-ventas-comision"
          csv={monthRows.map(csvRow)}
        >
          {chartMonths.length === 0 ? (
            empty(NO_SALES)
          ) : (
            <ComboChart
              data={chartMonths}
              xKey="label"
              height={260}
              rightFormat={axisPct}
              series={[
                { key: 'sales', label: 'Ventas', type: 'bar', color: COLOR.blue, format: money },
                { key: 'fees', label: 'Comisión', type: 'bar', color: COLOR.amber, format: money },
                { key: 'pct', label: '% efectivo', type: 'line', axis: 'right', color: COLOR.bad, format: pct1 },
              ]}
            />
          )}
        </ChartCard>
        <ChartCard
          title={`Comisión real vs estimada — ${description}`}
          subtitle="La estimada aún no está en un reporte de conciliación de Walmart (se infiere por el 15 % exacto)."
          filename="walmart-comision-real-estimada"
          csv={monthRows.map((m) => ({ Mes: m.month, Real: m.fees_actual, Estimada: m.fees_estimated }))}
        >
          {chartMonths.length === 0 ? (
            empty(NO_SALES)
          ) : (
            <ComboChart
              data={chartMonths}
              xKey="label"
              height={260}
              series={[
                { key: 'fees_actual', label: 'Real', type: 'bar', color: COLOR.good, stack: 'f', format: money },
                { key: 'fees_estimated', label: 'Estimada', type: 'bar', color: COLOR.amber, stack: 'f', format: money },
              ]}
            />
          )}
        </ChartCard>
        <ChartCard
          title={`Ajustes por tipo — ${description}`}
          subtitle="Reembolsos y cargos de etiqueta de Walmart. Otros ajustes del reporte no se guardan."
          filename="walmart-ajustes"
          csv={(data?.by_adjustment ?? []).map((a) => ({ Tipo: a.label, Monto: a.amount }))}
        >
          {slices.length === 0 ? empty('No hay ajustes en este período.') : <Donut slices={slices} format={money} centerLabel="ajustes" />}
        </ChartCard>
        <Card>
          <CardContent className="space-y-2 pt-4 text-sm">
            <div className="font-medium">Neto esperado a cobrar</div>
            <div className="text-2xl font-semibold tabular-nums">{k ? money(k.net_expected) : '—'}</div>
            <Hint>Ventas − comisiones − reembolsos − etiquetas de Walmart. Es lo esperado, no un pago confirmado.</Hint>
            {data && (
              <div>
                <div className="mt-3 font-medium">Qué falta para cuadrar con Walmart</div>
                <ul className="text-muted-foreground list-disc space-y-1 pl-5 text-xs">
                  {data.missing.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="space-y-3 overflow-x-auto pt-4">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Detalle por mes</span>
            <CsvButton filename="walmart-pagos-comisiones.csv" rows={monthRows.map(csvRow)} />
          </div>
          {monthRows.length > 0 ? (
            <MonthTable months={monthRows} />
          ) : (
            <p className="text-muted-foreground py-6 text-center text-sm">{report.loading ? 'Cargando…' : NO_SALES}</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
