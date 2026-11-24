import { useMemo, useState } from 'react'

import { Callout, ErrorAlert } from '@/components/analytics/ui'
import { ChartCard, axisMoney, axisNumber, COLOR } from '@/components/charts/kit'
import { ComboChart } from '@/components/charts/ComboChart'
import { HeatGrid } from '@/components/charts/HeatGrid'
import { AsyncBody, HBars } from '@/components/operacion/shared'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, type Filters } from '@/lib/api'
import { count, money } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

type Metric = 'orders' | 'gross_sales'

const DAYS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
const HOURS = Array.from({ length: 24 }, (_, h) => h)
const hourLabel = (h: number) => `${String(h).padStart(2, '0')}:00`
const WINDOW = 3 // horas seguidas de la "mejor franja"

export function Dayparting() {
  const scope = useScope()
  const [metric, setMetric] = useState<Metric>('orders')
  const [picked, setPicked] = useState<number | null>(null)

  const { start, end } = scope.range
  const filters = useMemo<Filters>(
    () => ({ start, end, channel: scope.channelParam, account: scope.account }),
    [start, end, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.dayparting(filters), [filters])

  const isOrders = metric === 'orders'
  const fmt = isOrders ? count : money
  const label = isOrders ? 'Órdenes' : 'Ventas'

  const { values, hourly, perHour } = useMemo(() => {
    const grid = DAYS.map(() => HOURS.map(() => 0))
    const hours = HOURS.map((h) => ({ h, orders: 0, units: 0, gross_sales: 0 }))
    for (const c of data.data?.cells ?? []) {
      if (c.weekday < 0 || c.weekday > 6 || c.hour < 0 || c.hour > 23) continue
      grid[c.weekday][c.hour] = c[metric]
      hours[c.hour].orders += c.orders
      hours[c.hour].units += c.units
      hours[c.hour].gross_sales += c.gross_sales
    }
    const avg = hours.reduce((a, x) => a + x[metric], 0) / 24
    return {
      values: grid,
      hourly: hours,
      perHour: hours.map((x) => ({ hora: hourLabel(x.h), h: x.h, value: x[metric], avg })),
    }
  }, [data.data, metric])

  // Mejor franja: las 3 horas seguidas con más actividad, sumando todos los días.
  const best = useMemo(() => {
    let top = { start: 0, total: -1 }
    for (let s = 0; s + WINDOW <= 24; s++) {
      const total = hourly.slice(s, s + WINDOW).reduce((a, x) => a + x[metric], 0)
      if (total > top.total) top = { start: s, total }
    }
    const share = hourly.reduce((a, x) => a + x[metric], 0)
    return { ...top, share: share > 0 ? top.total / share : 0 }
  }, [hourly, metric])

  const coverage = data.data?.coverage
  const missing = coverage ? coverage.total - coverage.with_time : 0
  const empty = (data.data?.cells.length ?? 0) === 0
  const hourRow = picked != null ? hourly[picked] : null

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Dayparting: horas pico</h1>

      <ErrorAlert message={data.error} />

      {coverage && missing > 0 && (
        <Callout tone="warning">
          {count(missing)} de {count(coverage.total)} órdenes no tienen hora registrada (vienen de un CSV o se
          sincronizaron antes de esta versión). Vuelve a sincronizar TikTok y Walmart desde Importar para completarlas.
        </Callout>
      )}

      <Tabs value={metric} onValueChange={(v) => setMetric(v as Metric)}>
        <TabsList>
          <TabsTrigger value="orders">Por órdenes</TabsTrigger>
          <TabsTrigger value="gross_sales">Por ventas</TabsTrigger>
        </TabsList>
      </Tabs>

      {!empty && best.total > 0 && (
        <Callout tone="info">
          <strong>
            Mejor franja: {hourLabel(best.start)} a {hourLabel(best.start + WINDOW)}
          </strong>{' '}
          con {fmt(best.total)} ({(best.share * 100).toFixed(0)}% de {isOrders ? 'las órdenes' : 'las ventas'} del período). Programa
          publicaciones, lives y anuncios en esa franja.
        </Callout>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        {(data.data?.best_hours ?? []).map((b, i) => (
          <Card key={b.hour}>
            <CardHeader>
              <CardTitle className="text-sm font-medium">
                #{i + 1} hora pico · {hourLabel(b.hour)}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-semibold">{count(b.orders)} órdenes</div>
              <div className="text-muted-foreground text-xs">
                {money(b.gross_sales)} en ventas · {count(b.units)} unidades
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <ChartCard
        title={`Mapa de calor día × hora (${isOrders ? 'órdenes' : 'ventas'})`}
        subtitle={`Zona horaria ${data.data?.timezone ?? '—'}. Más oscuro = más ${isOrders ? 'órdenes' : 'ventas'}. Clic en una celda para ver esa hora por día.`}
        csv={(data.data?.cells ?? []).map((c) => ({ dia: DAYS[c.weekday], hora: hourLabel(c.hour), ordenes: c.orders, unidades: c.units, ventas: c.gross_sales }))}
        filename="horas-pico-mapa"
      >
        <AsyncBody state={data} empty={empty}>
          <HeatGrid
            rowLabels={DAYS}
            colLabels={HOURS.map(String)}
            values={values}
            format={fmt}
            unit={isOrders ? 'órdenes' : 'en ventas'}
            onCellClick={(_r, c) => setPicked((p) => (p === c ? null : c))}
          />
        </AsyncBody>
      </ChartCard>

      <ChartCard
        title={`${label} por hora del día`}
        subtitle="La línea es el promedio por hora; las barras que la superan son tus horas fuertes. Clic en una barra para ver el detalle por día."
        csv={hourly.map((x) => ({ hora: hourLabel(x.h), ordenes: x.orders, unidades: x.units, ventas: x.gross_sales }))}
        filename="horas-pico-por-hora"
      >
        <AsyncBody state={data} empty={empty}>
          <ComboChart
            data={perHour}
            xKey="hora"
            xFormat={(v) => String(v).slice(0, 2)}
            leftFormat={isOrders ? axisNumber : axisMoney}
            series={[
              { key: 'value', label, type: 'bar', color: COLOR.blue, format: fmt },
              { key: 'avg', label: 'Promedio por hora', type: 'line', color: COLOR.amber, format: fmt },
            ]}
            onPointClick={(row) => setPicked((p) => (p === Number(row.h) ? null : Number(row.h)))}
          />
        </AsyncBody>
      </ChartCard>

      {picked != null && hourRow && (
        <ChartCard
          title={`Detalle de las ${hourLabel(picked)}`}
          subtitle={`${count(hourRow.orders)} órdenes · ${money(hourRow.gross_sales)} en ventas · ${count(hourRow.units)} unidades en todo el período.`}
          csv={DAYS.map((d, i) => ({ dia: d, [isOrders ? 'ordenes' : 'ventas']: values[i][picked] }))}
          filename={`horas-pico-${picked}`}
          actions={
            <button type="button" onClick={() => setPicked(null)} className="text-muted-foreground hover:text-foreground rounded px-2 text-xs hover:bg-slate-100">
              Cerrar
            </button>
          }
        >
          <HBars data={DAYS.map((d, i) => ({ id: d, label: d, value: values[i][picked], color: COLOR.blue }))} format={fmt} valueLabel={label} axisFormat={isOrders ? axisNumber : axisMoney} />
        </ChartCard>
      )}
    </div>
  )
}
