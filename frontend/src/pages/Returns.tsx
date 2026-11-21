import { useMemo, useState } from 'react'

import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { FilterBar } from '@/components/FilterBar'
import { useScope } from '@/lib/scope'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, type Filters } from '@/lib/api'
import { count, money, percent, presetRange, type Preset } from '@/lib/format'
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
  const [preset, setPreset] = useState<Preset>('90d')
  const scope = useScope()
  const filters = useMemo<Filters>(
    () => ({ ...presetRange(preset), channel: scope.channelParam, account: scope.account }),
    [preset, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.returns(filters), [filters])
  const sm = data.data?.summary
  const reasons = data.data?.by_reason ?? []
  const returnReasons = reasons.filter((r) => r.kind === 'return')
  const cancelReasons = reasons.filter((r) => r.kind === 'cancellation')
  const products = data.data?.by_product ?? []

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Devoluciones y cancelaciones (RMA)</h1>
        <FilterBar preset={preset} onPreset={setPreset} />
      </div>
      <ChannelOnlyNotice channels={['tiktok_shop']} what="Devoluciones y cancelaciones" />

      {data.error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {data.error}
        </div>
      )}

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

      <div className="grid gap-4 xl:grid-cols-2">
        {[
          { title: 'Motivos de devolución', rows: returnReasons },
          { title: 'Motivos de cancelación', rows: cancelReasons },
        ].map((block) => (
          <Card key={block.title}>
            <CardHeader>
              <CardTitle>{block.title}</CardTitle>
            </CardHeader>
            <CardContent>
              {block.rows.length === 0 ? (
                <p className="text-muted-foreground text-sm">{data.loading ? 'Cargando…' : 'Sin casos en el periodo.'}</p>
              ) : (
                <ul className="divide-y text-sm">
                  {block.rows.map((r) => (
                    <li key={r.reason} className="flex items-center justify-between gap-3 py-1.5">
                      <span className="min-w-0 truncate" title={r.reason}>
                        {r.reason}
                      </span>
                      <span className="shrink-0">
                        <span className="font-medium">{count(r.cases)}</span>
                        <span className="text-muted-foreground ml-2 text-xs">{money(r.refund_total)}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

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
          <CardTitle>Casos recientes</CardTitle>
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
              {(data.data?.recent ?? []).map((c) => (
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

      <p className="text-muted-foreground max-w-3xl text-xs">
        Por ahora solo TikTok Shop informa devoluciones y cancelaciones. Las solicitudes que el comprador retiró no
        cuentan. La tasa divide las unidades devueltas en el periodo entre las vendidas en el mismo periodo, así que
        puede variar un poco si la devolución llega después.
      </p>
    </div>
  )
}
