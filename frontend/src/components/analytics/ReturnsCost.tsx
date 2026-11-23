import { useMemo, useState } from 'react'

import { ErrorAlert, Hint, Kpi, NumberField, PILL, Pill, pct1 } from '@/components/analytics/ui'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { analytics } from '@/lib/analytics'
import { count, money } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

/** Costo real de las devoluciones: envío perdido y producto que no se puede revender. */
export function ReturnsCost() {
  const scope = useScope()
  const [lossRate, setLossRate] = useState(0.5)
  const sc = useMemo(
    () => ({ channel: scope.channelParam, account: scope.account }),
    [scope.channelParam, scope.account],
  )
  const data = useAsync(() => analytics.returnsCost(sc, 90, lossRate), [sc, lossRate])
  const d = data.data
  const sm = d?.summary

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 className="text-xl font-semibold">Costo real de las devoluciones (90 días)</h2>
        <NumberField
          label="Pérdida si el motivo no se reconoce"
          value={Math.round(lossRate * 100)}
          min={0}
          max={100}
          suffix="%"
          onCommit={(v) => setLossRate(v / 100)}
        />
      </div>
      <ErrorAlert message={data.error} />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Reembolsado" value={money(sm?.refund ?? 0)} hint={`${count(sm?.cases ?? 0)} devoluciones · ${count(sm?.units ?? 0)} unidades`} />
        <Kpi title="Envío perdido" value={money(sm?.shipping_lost ?? 0)} hint="etiqueta de ida que no se recupera" />
        <Kpi title="Producto perdido" value={money(sm?.product_lost ?? 0)} hint="dañado, defectuoso o no revendible" />
        <Kpi
          title="Costo real"
          value={money(sm?.real_cost ?? 0)}
          hint={`${count(sm?.seller_fault_units ?? 0)} unidades por culpa del vendedor`}
          className={(sm?.real_cost ?? 0) > 0 ? 'text-red-600' : ''}
        />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Por motivo</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Motivo</TableHead>
                  <TableHead className="text-right">Casos</TableHead>
                  <TableHead className="text-right">Pérdida</TableHead>
                  <TableHead className="text-right">Costo real</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(d?.by_reason.length ?? 0) === 0 && (
                  <TableRow>
                    <TableCell colSpan={4} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Sin devoluciones en el periodo.'}
                    </TableCell>
                  </TableRow>
                )}
                {(d?.by_reason ?? []).map((r) => (
                  <TableRow key={r.reason}>
                    <TableCell>
                      <div className="flex flex-col">
                        <span className="font-medium">{r.reason}</span>
                        {r.seller_fault && <Pill label="culpa del vendedor" className={PILL.amber} />}
                      </div>
                    </TableCell>
                    <TableCell className="text-right">{count(r.cases)}</TableCell>
                    <TableCell className="text-right">
                      {pct1(r.loss_rate)}
                      {!r.known_reason && <span className="text-muted-foreground text-xs"> (supuesto)</span>}
                    </TableCell>
                    <TableCell className="text-right">{money(r.real_cost)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Productos que más cuestan</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Producto</TableHead>
                  <TableHead className="text-right">Devueltas</TableHead>
                  <TableHead className="text-right">Tasa</TableHead>
                  <TableHead className="text-right">Costo real</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(d?.by_sku ?? []).slice(0, 10).map((s) => (
                  <TableRow key={s.sku}>
                    <TableCell>
                      <div className="flex min-w-40 flex-col">
                        <span className="font-medium">{s.product_name ?? s.sku}</span>
                        <span className="text-muted-foreground text-xs">
                          {s.sku}
                          {!s.has_cost && ' · sin costo cargado'}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right">{count(s.returned_units)}</TableCell>
                    <TableCell className="text-right">{pct1(s.return_rate)}</TableCell>
                    <TableCell className="text-right">{money(s.real_cost)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
      <Hint>
        El reembolso devuelve el ingreso, pero tú ya pagaste la etiqueta de ida y, si el producto no se puede revender,
        pierdes su costo. Costo real = envío perdido (la etiqueta de la orden en proporción a las unidades devueltas) +
        producto perdido (costo × tasa de pérdida según el motivo: dañado, defectuoso o no como se describió = 100 %;
        arrepentimiento del cliente = 0 %). No incluye la etiqueta de regreso ni el tiempo de manejo.
      </Hint>
    </section>
  )
}
