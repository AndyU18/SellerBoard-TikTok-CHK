import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill, Segmented, pct1 } from '@/components/analytics/ui'
import { stateCode, UsTileMap } from '@/components/charts/UsTileMap'
import { axisMoney, ChartCard } from '@/components/charts/kit'
import { AsyncBody, HBars, rangeDays } from '@/components/operacion/shared'
import { chartdata } from '@/lib/chartdataOperacion'
import type { ShippingRow } from '@/lib/analytics'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { analytics } from '@/lib/analytics'
import { count, money, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

type MapMetric = 'per_order' | 'share' | 'contribution'
const METRICS: Record<MapMetric, { label: string; value: (r: ShippingRow) => number; format: (v: number) => string }> = {
  per_order: { label: 'Costo por orden', value: (r) => r.shipping_per_order ?? 0, format: money },
  share: { label: '% de la venta', value: (r) => r.shipping_share ?? 0, format: (v) => pct1(v) },
  contribution: { label: 'Contribución', value: (r) => r.contribution, format: money },
}
const CHANNEL_LABEL: Record<string, string> = { tiktok_shop: 'TikTok Shop', walmart: 'Walmart', shopify: 'Shopify', amazon: 'Amazon' }

/** Cuánto del precio se va en el envío, por estado de destino y por producto. */
export function ShippingProfit() {
  const scope = useScope()
  const sc = useMemo(
    () => ({ channel: scope.channelParam, account: scope.account }),
    [scope.channelParam, scope.account],
  )
  const { start, end } = scope.range
  const days = rangeDays({ start, end })
  const data = useAsync(() => analytics.shipping(sc, days), [sc, days])
  const breakdown = useAsync(
    () => chartdata.shippingBreakdown({ start, end, channel: scope.channelParam, account: scope.account }),
    [start, end, scope.channelParam, scope.account],
  )
  const [metric, setMetric] = useState<MapMetric>('per_order')
  const [picked, setPicked] = useState<string | null>(null)
  const d = data.data
  const sm = d?.summary
  const states = useMemo(() => d?.by_state ?? [], [d])
  const M = METRICS[metric]
  const mapData = states.map((r) => ({ state: r.key, value: M.value(r) }))
  const byCode = new Map(states.map((r) => [stateCode(r.key) ?? r.key, r]))
  const pickedCode = picked ? stateCode(picked) : null
  const tableRows = pickedCode ? states.filter((r) => stateCode(r.key) === pickedCode) : states
  const channelBars = (breakdown.data?.by_channel ?? []).map((r) => ({
    id: r.key,
    label: CHANNEL_LABEL[r.key] ?? r.key,
    value: r.shipping_per_order ?? 0,
    extra: `${money(r.shipping)} en ${count(r.orders)} órdenes · ${pct1(r.shipping_share)} de la venta`,
  }))
  const bandBars = (breakdown.data?.by_band ?? []).map((r) => ({
    id: r.key,
    label: r.key,
    value: r.orders,
    extra: `${money(r.shipping)} de envío · ${pct1(r.shipping_share)} de la venta`,
  }))

  return (
    <section className="space-y-4">
      <h2 className="text-xl font-semibold">Rentabilidad del envío ({days} días)</h2>
      <ErrorAlert message={data.error} />
      {sm && (sm.state_coverage ?? 0) < 0.9 && (
        <Callout tone="info">
          El estado de destino solo está en el {pct1(sm.state_coverage)} de las órdenes: las demás se sincronizaron antes
          de activar esta función y aparecen como “(sin dato)”. Se completa con cada sincronización.
        </Callout>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Envío total" value={money(sm?.shipping ?? 0)} hint={`${count(sm?.orders ?? 0)} órdenes`} />
        <Kpi
          title="Envío sobre ventas netas"
          value={pct1(sm && sm.net_sales > 0 ? sm.shipping / sm.net_sales : null)}
          hint="lo que se va en etiquetas"
        />
        <Kpi
          title="Productos con envío caro"
          value={count(sm?.skus_over_threshold ?? 0)}
          hint={`el envío pasa de ${pct1(d?.threshold)} del precio`}
          className={(sm?.skus_over_threshold ?? 0) > 0 ? 'text-amber-600' : ''}
        />
        <Kpi title="Órdenes con estado" value={pct1(sm?.state_coverage)} hint={`${count(sm?.orders_with_state ?? 0)} de ${count(sm?.orders ?? 0)}`} />
      </div>
      <ChartCard
        title="Mapa de EE. UU. por estado de destino"
        subtitle="Cambia la métrica con los botones; clic en un estado para filtrar la tabla de abajo."
        csv={states.map((r) => ({ estado: r.key, ordenes: r.orders, envio_por_orden: r.shipping_per_order, envio_sobre_ventas: r.shipping_share, contribucion: r.contribution, margen: r.margin }))}
        filename="envio-por-estado"
        actions={
          <Segmented
            label="Métrica del mapa"
            value={metric}
            options={(Object.keys(METRICS) as MapMetric[]).map((k) => ({ value: k, label: METRICS[k].label }))}
            onChange={setMetric}
          />
        }
      >
        <AsyncBody state={data} empty={states.filter((r) => stateCode(r.key)).length === 0}>
          <UsTileMap
            data={mapData}
            format={M.format}
            selected={picked}
            onStateClick={(name) => setPicked((p) => (p && stateCode(p) === stateCode(name) ? null : name))}
            describe={(name, value) => {
              const r = byCode.get(stateCode(name) ?? '')
              return r
                ? `${name}: ${M.format(value ?? 0)} ${M.label.toLowerCase()} · ${count(r.orders)} órdenes · envío ${pct1(r.shipping_share)} de la venta · margen ${pct1(r.margin)}`
                : `${name}: sin órdenes`
            }}
          />
        </AsyncBody>
      </ChartCard>
      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Envío por orden según el canal"
          subtitle="Costo promedio de envío (línea + etiqueta) por orden."
          csv={(breakdown.data?.by_channel ?? []).map((r) => ({ canal: r.key, ordenes: r.orders, envio_total: r.shipping, envio_por_orden: r.shipping_per_order, envio_sobre_ventas: r.shipping_share }))}
          filename="envio-por-canal"
        >
          <AsyncBody state={breakdown} empty={channelBars.length === 0}>
            <HBars data={channelBars} format={money} valueLabel="Envío por orden" axisFormat={axisMoney} />
          </AsyncBody>
        </ChartCard>
        <ChartCard
          title="Órdenes por rango de costo de envío"
          subtitle="Cuántas órdenes cayeron en cada rango de costo de envío (el peso del paquete no viene en los datos)."
          csv={(breakdown.data?.by_band ?? []).map((r) => ({ rango: r.key, ordenes: r.orders, envio_total: r.shipping, envio_sobre_ventas: r.shipping_share }))}
          filename="envio-por-rango"
        >
          <AsyncBody state={breakdown} empty={bandBars.every((b) => b.value === 0)}>
            <HBars data={bandBars} format={count} valueLabel="Órdenes" />
          </AsyncBody>
        </ChartCard>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2">
            <CardTitle>Por estado de destino{picked ? ` · ${picked}` : ''}</CardTitle>
            {picked && (
              <button type="button" onClick={() => setPicked(null)} className="text-muted-foreground hover:text-foreground rounded px-2 text-xs hover:bg-slate-100">
                Quitar filtro
              </button>
            )}
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Estado</TableHead>
                  <TableHead className="text-right">Órdenes</TableHead>
                  <TableHead className="text-right">Envío / orden</TableHead>
                  <TableHead className="text-right">Envío / ventas</TableHead>
                  <TableHead className="text-right">Margen</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tableRows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Sin órdenes para este estado.'}
                    </TableCell>
                  </TableRow>
                )}
                {tableRows.slice(0, 15).map((r) => (
                  <TableRow key={r.key}>
                    <TableCell className="font-medium">{r.key}</TableCell>
                    <TableCell className="text-right">{count(r.orders)}</TableCell>
                    <TableCell className="text-right">{money(r.shipping_per_order)}</TableCell>
                    <TableCell className="text-right">{pct1(r.shipping_share)}</TableCell>
                    <TableCell className={`text-right ${tone(r.margin)}`}>{pct1(r.margin)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Productos donde el envío pesa más</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Producto</TableHead>
                  <TableHead className="text-right">Envío / unidad</TableHead>
                  <TableHead className="text-right">Envío / ventas</TableHead>
                  <TableHead className="text-right">Margen</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(d?.by_sku ?? []).slice(0, 12).map((r) => (
                  <TableRow key={r.key}>
                    <TableCell>
                      <div className="flex min-w-40 flex-col">
                        <span className="font-medium">{r.product_name ?? r.key}</span>
                        <span className="text-muted-foreground text-xs">{r.key}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right">{money(r.shipping_per_unit)}</TableCell>
                    <TableCell className="text-right">
                      {r.flag ? <Pill label={pct1(r.shipping_share)} className={PILL.amber} /> : pct1(r.shipping_share)}
                    </TableCell>
                    <TableCell className={`text-right ${tone(r.margin)}`}>{pct1(r.margin)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
      <Hint>
        El envío de cada línea es el de la plataforma más la parte de la etiqueta de su orden (según sus ventas). El
        margen es la contribución después de comisiones, envío, reembolsos y costo del producto sobre las ventas netas.
        Un envío alto frente al precio sugiere subir el precio, ofrecer paquetes o cambiar de transportista.
      </Hint>
    </section>
  )
}
