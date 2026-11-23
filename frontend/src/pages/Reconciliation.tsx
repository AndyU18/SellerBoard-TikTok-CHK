import { ChannelBadge } from '@/components/ChannelBadge'
import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill, pct1 } from '@/components/analytics/ui'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { analytics, type LabelKind } from '@/lib/analytics'
import { count, money, tone } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const LABEL_KIND: Record<LabelKind, { label: string; explain: string; pill: string }> = {
  order: {
    label: 'Orden de venta',
    explain: 'Ya está en la utilidad de su orden.',
    pill: PILL.green,
  },
  sample: {
    label: 'Muestra a creador',
    explain: 'Su costo se ve en Muestras; no entra a la utilidad.',
    pill: PILL.blue,
  },
  shipment_without_order: {
    label: 'Envío sin orden de venta',
    explain:
      'Hay envío pero la orden aún no se liquida (o se canceló). Suele ser dinero ya gastado con ingreso pendiente.',
    pill: PILL.amber,
  },
  unmatched: {
    label: 'Sin coincidencia',
    explain: 'La etiqueta no corresponde a ninguna orden ni envío conocidos: órdenes viejas, otra tienda o un error.',
    pill: PILL.red,
  },
}

export function Reconciliation() {
  const data = useAsync(() => analytics.reconciliation(), [])
  const d = data.data
  const sm = d?.summary
  const payments = [...(d?.settlements.failed ?? []), ...(d?.settlements.stuck ?? [])]

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Conciliación y fugas</h1>
      <ErrorAlert message={data.error} />

      {((sm?.failed_payments ?? 0) > 0 || (sm?.stuck_payments ?? 0) > 0) && (
        <Callout tone="danger">
          <strong>
            {money(sm?.failed_payments ?? 0)} en pagos fallidos y {money(sm?.stuck_payments ?? 0)} sin depositar tras{' '}
            {d?.settlements.stuck_days} días.
          </strong>{' '}
          Revisa la cuenta bancaria en el panel de cada plataforma.
        </Callout>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Etiquetas sin explicar"
          value={money(sm?.unassigned_labels_total ?? 0)}
          hint={`${count(sm?.unassigned_labels_count ?? 0)} órdenes de Redo sin venta asociada`}
          className={(sm?.unassigned_labels_total ?? 0) > 0 ? 'text-amber-600' : ''}
        />
        <Kpi
          title="Líneas sin comisión"
          value={count(sm?.zero_fee_lines ?? 0)}
          hint="venta con comisión $0 (posible dato faltante)"
          className={(sm?.zero_fee_lines ?? 0) > 0 ? 'text-amber-600' : ''}
        />
        <Kpi
          title="Comisión anormal"
          value={count(sm?.high_fee_lines ?? 0)}
          hint="más del doble de la tasa típica del canal"
          className={(sm?.high_fee_lines ?? 0) > 0 ? 'text-amber-600' : ''}
        />
        <Kpi
          title="Canales con cambio de tasa"
          value={count(sm?.fee_drift_channels ?? 0)}
          hint="2 puntos o más contra las semanas anteriores"
          className={(sm?.fee_drift_channels ?? 0) > 0 ? 'text-red-600' : ''}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Cargos de etiquetas (Redo) según su orden</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tipo</TableHead>
                <TableHead>Qué significa</TableHead>
                <TableHead className="text-right">Órdenes</TableHead>
                <TableHead className="text-right">Monto</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(Object.keys(LABEL_KIND) as LabelKind[]).map((k) => (
                <TableRow key={k}>
                  <TableCell>
                    <Pill label={LABEL_KIND[k].label} className={LABEL_KIND[k].pill} />
                  </TableCell>
                  <TableCell className="text-muted-foreground max-w-md text-sm">{LABEL_KIND[k].explain}</TableCell>
                  <TableCell className="text-right">{count(d?.labels.summary[k].orders ?? 0)}</TableCell>
                  <TableCell className="text-right">{money(d?.labels.summary[k].amount ?? 0)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Etiquetas más caras sin orden de venta</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Orden</TableHead>
                <TableHead>Tipo</TableHead>
                <TableHead>Cobrada</TableHead>
                <TableHead className="text-right">Monto</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(d?.labels.unmatched.length ?? 0) === 0 && (
                <TableRow>
                  <TableCell colSpan={4} className="text-muted-foreground py-8 text-center">
                    {data.loading ? 'Cargando…' : 'No hay etiquetas sin explicar.'}
                  </TableCell>
                </TableRow>
              )}
              {(d?.labels.unmatched ?? []).slice(0, 25).map((u) => (
                <TableRow key={u.order_id}>
                  <TableCell className="font-mono text-xs">{u.order_id}</TableCell>
                  <TableCell>
                    <Pill label={LABEL_KIND[u.kind].label} className={LABEL_KIND[u.kind].pill} />
                  </TableCell>
                  <TableCell>{u.charged ?? '—'}</TableCell>
                  <TableCell className="text-right">{money(u.amount)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Tasa de comisión por canal</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Canal</TableHead>
                  <TableHead className="text-right">Líneas</TableHead>
                  <TableHead className="text-right">Típica</TableHead>
                  <TableHead className="text-right">Reciente</TableHead>
                  <TableHead className="text-right">Anterior</TableHead>
                  <TableHead className="text-right">Cambio</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(d?.fees.channels ?? []).map((c) => (
                  <TableRow key={c.channel}>
                    <TableCell>
                      <ChannelBadge channel={c.channel} />
                    </TableCell>
                    <TableCell className="text-right">{count(c.lines)}</TableCell>
                    <TableCell className="text-right">{pct1(c.median_rate)}</TableCell>
                    <TableCell className="text-right">{pct1(c.recent_rate)}</TableCell>
                    <TableCell className="text-right">{pct1(c.baseline_rate)}</TableCell>
                    <TableCell className={`text-right ${c.drift_alert ? 'font-medium text-red-600' : tone(c.drift)}`}>
                      {c.drift == null ? '—' : `${c.drift > 0 ? '+' : ''}${(c.drift * 100).toFixed(1)} pts`}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Pagos fallidos y atorados</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Canal</TableHead>
                  <TableHead>Estado de cuenta</TableHead>
                  <TableHead>Cierre</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead className="text-right">Monto</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {payments.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Sin pagos fallidos ni atorados.'}
                    </TableCell>
                  </TableRow>
                )}
                {payments.map((s) => (
                  <TableRow key={s.external_id}>
                    <TableCell>
                      <ChannelBadge channel={s.channel} />
                    </TableCell>
                    <TableCell className="font-mono text-xs">{s.external_id}</TableCell>
                    <TableCell>{s.statement_date ?? '—'}</TableCell>
                    <TableCell>
                      <Pill
                        label={s.status === 'failed' ? 'Fallido' : 'Sin depositar'}
                        className={s.status === 'failed' ? PILL.red : PILL.amber}
                      />
                    </TableCell>
                    <TableCell className="text-right">{money(s.net_payout)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Líneas con comisión anormal</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Orden</TableHead>
                  <TableHead>SKU</TableHead>
                  <TableHead className="text-right">Venta</TableHead>
                  <TableHead className="text-right">Comisión</TableHead>
                  <TableHead className="text-right">Tasa (típica)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(d?.fees.high_fee.length ?? 0) === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Sin comisiones fuera de lo normal.'}
                    </TableCell>
                  </TableRow>
                )}
                {(d?.fees.high_fee ?? []).slice(0, 15).map((l) => (
                  <TableRow key={`${l.order_id}-${l.sku}`}>
                    <TableCell className="font-mono text-xs">{l.order_id}</TableCell>
                    <TableCell>{l.sku}</TableCell>
                    <TableCell className="text-right">{money(l.gross)}</TableCell>
                    <TableCell className="text-right">{money(l.fees)}</TableCell>
                    <TableCell className="text-right">
                      {pct1(l.rate)} <span className="text-muted-foreground">({pct1(l.typical)})</span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Líneas con comisión $0</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Orden</TableHead>
                  <TableHead>SKU</TableHead>
                  <TableHead>Fecha</TableHead>
                  <TableHead className="text-right">Venta</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(d?.fees.zero_fee.length ?? 0) === 0 && (
                  <TableRow>
                    <TableCell colSpan={4} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Todas las ventas traen comisión.'}
                    </TableCell>
                  </TableRow>
                )}
                {(d?.fees.zero_fee ?? []).slice(0, 15).map((l) => (
                  <TableRow key={`${l.order_id}-${l.sku}`}>
                    <TableCell className="font-mono text-xs">{l.order_id}</TableCell>
                    <TableCell>{l.sku}</TableCell>
                    <TableCell>{l.date}</TableCell>
                    <TableCell className="text-right">{money(l.gross)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <Hint>
        Cada cargo de etiqueta se cruza por número de orden con tus ventas y con los envíos de la plataforma. Los “sin
        orden de venta” no siempre son fuga: en TikTok Shop solo entran a la utilidad las órdenes ya liquidadas, así que
        la etiqueta de una orden pendiente aparece aquí hasta que se liquide.
      </Hint>
    </div>
  )
}
