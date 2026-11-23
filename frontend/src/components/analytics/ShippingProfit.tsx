import { useMemo } from 'react'

import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill, pct1 } from '@/components/analytics/ui'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { analytics } from '@/lib/analytics'
import { count, money, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

/** Cuánto del precio se va en el envío, por estado de destino y por producto. */
export function ShippingProfit() {
  const scope = useScope()
  const sc = useMemo(
    () => ({ channel: scope.channelParam, account: scope.account }),
    [scope.channelParam, scope.account],
  )
  const data = useAsync(() => analytics.shipping(sc, 90), [sc])
  const d = data.data
  const sm = d?.summary

  return (
    <section className="space-y-4">
      <h2 className="text-xl font-semibold">Rentabilidad del envío (90 días)</h2>
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
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Por estado de destino</CardTitle>
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
                {(d?.by_state ?? []).slice(0, 15).map((r) => (
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
