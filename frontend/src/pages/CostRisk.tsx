import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, pct1 } from '@/components/analytics/ui'
import { BubbleChart, type BubblePoint } from '@/components/charts/BubbleChart'
import { ComboChart } from '@/components/charts/ComboChart'
import { axisPct, ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { costRisk, type CostRiskSku } from '@/lib/costRisk'
import { count, money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

/** Cuántos SKU dibuja la curva de cobertura (más allá de esto las barras no se distinguen). */
const CURVE_SKUS = 30
/** Máximo de filas que pinta la tabla de una vez; la búsqueda y el CSV cubren todas. */
const TABLE_ROWS = 200
/** Un SKU cuyo margen ajustado (con el costo estimado) baja de este valor se resalta en rojo. */
const THIN_MARGIN = 0.1
const ESTIMATE_WARNING =
  'El costo de estos SKU es una estimación, no un dato real: se calcula con el peso mediano del costo sobre la venta de los SKU que sí tienen costo. Úsalo para decidir qué cargar primero, no como cifra contable.'

const csvCell = (v: string | number | null | undefined): string => {
  const s = v == null ? '' : String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

function matches(row: CostRiskSku, text: string): boolean {
  const needle = text.trim().toLowerCase()
  return !needle || row.sku.toLowerCase().includes(needle) || (row.name ?? '').toLowerCase().includes(needle)
}

function downloadCsv(rows: CostRiskSku[], days: number) {
  const header = ['Prioridad', 'SKU', 'Producto', 'Unidades', 'Ventas netas', 'Utilidad reportada', 'Costo estimado por unidad', 'Utilidad ajustada', 'Utilidad en riesgo', '% acumulado de ventas sin costo']
  const line = (r: CostRiskSku) => [
    r.priority, r.sku, r.name, r.units, r.net_sales.toFixed(2), r.reported_profit.toFixed(2),
    r.est_unit_cost.toFixed(2), r.adjusted_profit.toFixed(2), r.profit_at_risk.toFixed(2),
    r.cumulative_sales_share == null ? '' : (r.cumulative_sales_share * 100).toFixed(1) + '%',
  ]
  const body = [header, ...rows.map(line)].map((l) => l.map(csvCell).join(',')).join('\r\n')
  const url = URL.createObjectURL(new Blob(['﻿' + body], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `costos-faltantes-${days}d.csv`
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function CostRisk({ onNavigate }: { onNavigate?: (page: string) => void }) {
  const { preset, channelParam, account } = useScope()
  const description = useScopeDescription()
  const days = daysFromPreset(preset)
  const [text, setText] = useState('')

  const data = useAsync(() => costRisk.report({ days, channel: channelParam, account }), [days, channelParam, account])
  const sm = data.data?.summary
  const skus = useMemo(() => data.data?.skus ?? [], [data.data])
  const visible = useMemo(() => skus.filter((r) => matches(r, text)), [skus, text])

  const stacked = useMemo(() => {
    if (!sm) return []
    const withCost = sm.reported_profit_total - sm.reported_profit_missing
    return [
      { label: 'Utilidad reportada', withCost, withoutCost: sm.reported_profit_missing },
      { label: 'Utilidad ajustada', withCost, withoutCost: sm.reported_profit_missing - sm.profit_at_risk },
    ]
  }, [sm])

  const curve = useMemo(
    () => skus.slice(0, CURVE_SKUS).map((r) => ({ n: r.priority, sku: r.sku, sales: r.net_sales, cumulative: r.cumulative_sales_share ?? 0 })),
    [skus],
  )

  const points: BubblePoint[] = useMemo(
    () => skus.map((r) => ({ id: r.sku, label: r.sku, x: r.net_sales, y: r.profit_at_risk, z: Math.max(1, r.units) })),
    [skus],
  )

  const empty = (msg: string) => <EmptyChart>{data.loading ? 'Cargando…' : msg}</EmptyChart>
  const noData = 'Ningún SKU con ventas le falta el costo.'
  const worse = sm && sm.overstated_pct != null && sm.overstated_pct > 0

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Utilidad en riesgo por costo faltante</h1>
          <p className="text-muted-foreground mt-1 text-sm">{description}</p>
        </div>
        {onNavigate && <Button onClick={() => onNavigate('costs')}>Cargar costos</Button>}
      </div>

      <Callout tone="warning">{ESTIMATE_WARNING}</Callout>
      <ErrorAlert message={data.error} />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Kpi
          title="Utilidad que depende de SKU sin costo"
          value={sm ? pct1(sm.pct_profit_missing) : '—'}
          className={worse ? 'text-red-600' : ''}
          hint={sm ? `${pct1(sm.pct_sales_missing)} de las ventas · ${money(sm.reported_profit_missing)} de utilidad reportada` : undefined}
        />
        <Kpi
          title="Utilidad reportada vs ajustada"
          value={sm ? `${money(sm.reported_profit_total)} → ${money(sm.adjusted_profit_total)}` : '—'}
          hint={sm ? `Inflada en ${money(sm.profit_at_risk)} (${pct1(sm.overstated_pct)}) · costo estimado ${pct1(sm.cost_ratio)} de la venta${sm.cost_ratio_source === 'fallback' ? ' (valor de respaldo: pocos SKU con costo)' : ''}` : undefined}
        />
        <Kpi
          title="SKU sin costo"
          value={sm ? count(sm.missing_skus) : '—'}
          hint={sm && sm.missing_skus > 0 ? `Cargar los primeros 5 cubre ${pct1(sm.coverage['5'])} de sus ventas; 10, ${pct1(sm.coverage['10'])}` : undefined}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title={`Utilidad con costo vs sin costo — ${description}`}
          subtitle="Cuánta utilidad viene de SKU con costo real (verde) y de SKU sin costo (ámbar). Al ajustar, la parte ámbar baja."
          filename="costos-faltantes-utilidad"
          csv={stacked.map((r) => ({ Utilidad: r.label, 'Con costo': r.withCost, 'Sin costo': r.withoutCost }))}
        >
          {stacked.length === 0 ? (
            empty(noData)
          ) : (
            <ComboChart
              data={stacked}
              xKey="label"
              height={260}
              series={[
                { key: 'withCost', label: 'SKU con costo', type: 'bar', color: COLOR.good, stack: 'a', format: money },
                { key: 'withoutCost', label: 'SKU sin costo', type: 'bar', color: COLOR.amber, stack: 'a', format: money },
              ]}
            />
          )}
        </ChartCard>

        <ChartCard
          title={`Qué cubres al cargar los primeros SKU — ${description}`}
          subtitle={`Barras: ventas de cada SKU sin costo, en orden de prioridad. Línea: % acumulado de las ventas sin costo que ya cubres (primeros ${CURVE_SKUS}).`}
          filename="costos-faltantes-cobertura"
          csv={skus.map((r) => ({ Prioridad: r.priority, SKU: r.sku, 'Ventas netas': r.net_sales, 'Acumulado': r.cumulative_sales_share }))}
        >
          {curve.length === 0 ? (
            empty(noData)
          ) : (
            <ComboChart
              data={curve}
              xKey="n"
              height={260}
              xFormat={(v) => `#${v}`}
              rightFormat={axisPct}
              series={[
                { key: 'sales', label: 'Ventas netas del SKU', type: 'bar', color: COLOR.blue, format: money },
                { key: 'cumulative', label: '% acumulado', type: 'line', axis: 'right', color: COLOR.bad, format: pct1 },
              ]}
            />
          )}
        </ChartCard>

        <ChartCard
          title={`Ventas vs utilidad en riesgo — ${description}`}
          subtitle="Arriba a la derecha están los SKU que más venden y más inflan la utilidad: los primeros que hay que cargar. El tamaño es el número de unidades. Clic para buscarlo en la tabla."
          filename="costos-faltantes-dispersion"
          className="xl:col-span-2"
          csv={skus.map((r) => ({ SKU: r.sku, 'Ventas netas': r.net_sales, 'Utilidad en riesgo': r.profit_at_risk, Unidades: r.units }))}
        >
          {points.length === 0 ? (
            empty(noData)
          ) : (
            <BubbleChart
              points={points}
              xLabel="Ventas netas"
              yLabel="Utilidad en riesgo"
              zLabel="Unidades"
              xFormat={money}
              yFormat={money}
              zFormat={count}
              height={280}
              onPointClick={(p) => setText(p.label)}
            />
          )}
        </ChartCard>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">SKU o producto</span>
            <Input className="h-9 w-full sm:w-64" value={text} placeholder="Ej. XMAS-8220" onChange={(e) => setText(e.target.value)} />
          </label>
          {text && (
            <Button variant="outline" onClick={() => setText('')}>
              Limpiar
            </Button>
          )}
          <Button onClick={() => downloadCsv(visible, days)} disabled={visible.length === 0} className="sm:ml-auto">
            Descargar (CSV)
          </Button>
        </CardContent>
      </Card>

      <div className="text-sm">
        <strong>{count(visible.length)}</strong> SKU en la tabla, en orden de prioridad de carga
        {visible.length > TABLE_ROWS && ` (se muestran los primeros ${TABLE_ROWS}; el CSV trae todos)`}
      </div>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-right">#</TableHead>
                <TableHead>SKU</TableHead>
                <TableHead>Producto</TableHead>
                <TableHead className="text-right">Un.</TableHead>
                <TableHead className="text-right">Ventas netas</TableHead>
                <TableHead className="text-right">Utilidad reportada</TableHead>
                <TableHead className="text-right">Costo est. / un.</TableHead>
                <TableHead className="text-right">Utilidad ajustada</TableHead>
                <TableHead className="text-right">En riesgo</TableHead>
                <TableHead className="text-right">% acumulado</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.length === 0 && (
                <TableRow>
                  <TableCell colSpan={10} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : skus.length === 0 ? noData : 'Ningún SKU coincide con la búsqueda.'}
                  </TableCell>
                </TableRow>
              )}
              {visible.slice(0, TABLE_ROWS).map((r) => (
                <TableRow key={r.sku}>
                  <TableCell className="text-right tabular-nums">{r.priority}</TableCell>
                  <TableCell className="font-mono text-xs whitespace-nowrap">{r.sku}</TableCell>
                  <TableCell className="max-w-64 truncate" title={r.name ?? undefined}>
                    {r.name ?? '—'}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{count(r.units)}</TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums">{money(r.net_sales)}</TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums">{money(r.reported_profit)}</TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums">{money(r.est_unit_cost)}</TableCell>
                  <TableCell
                    className={`text-right whitespace-nowrap tabular-nums ${r.adjusted_margin != null && r.adjusted_margin < THIN_MARGIN ? 'text-red-600' : ''}`}
                    title={r.adjusted_margin == null ? undefined : `Margen ajustado ${pct1(r.adjusted_margin)}`}
                  >
                    {money(r.adjusted_profit)}
                  </TableCell>
                  <TableCell className="text-right font-semibold whitespace-nowrap text-amber-700 tabular-nums">{money(r.profit_at_risk)}</TableCell>
                  <TableCell className="text-right tabular-nums">{pct1(r.cumulative_sales_share)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Hint>
        Utilidad = ventas netas − comisiones − envío − reembolsos − costo del producto, antes de publicidad y de la etiqueta de Redo
        (no se reparten por SKU). Un SKU está “sin costo” si su costo vigente hoy es 0. Rojo en la utilidad ajustada: margen menor a{' '}
        {pct1(THIN_MARGIN)}.
      </Hint>
    </div>
  )
}
