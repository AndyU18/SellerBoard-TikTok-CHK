import { useMemo, useState } from 'react'

import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { Callout, ErrorAlert, Hint, Kpi, NumberField, PILL, Pill, Segmented, days1 } from '@/components/analytics/ui'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { analytics, type ReorderStatus } from '@/lib/analytics'
import { count, money } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const STATUS: Record<ReorderStatus, { label: string; pill: string }> = {
  late: { label: 'Pedir ya (llegarás tarde)', pill: PILL.red },
  order_now: { label: 'Pedir ahora', pill: PILL.orange },
  ok: { label: 'Bien', pill: PILL.green },
  overstock: { label: 'Exceso', pill: PILL.amber },
  dead: { label: 'Sin ventas', pill: PILL.slate },
  no_demand: { label: 'Sin demanda ni stock', pill: PILL.slate },
}

export function Reorder() {
  const scope = useScope()
  const [lead, setLead] = useState(60)
  const [safety, setSafety] = useState(14)
  const [cover, setCover] = useState(60)
  const [multiplier, setMultiplier] = useState(1)
  const [status, setStatus] = useState<ReorderStatus | 'all'>('all')
  const [search, setSearch] = useState('')

  const sc = useMemo(
    () => ({ channel: scope.channelParam, account: scope.account }),
    [scope.channelParam, scope.account],
  )
  const data = useAsync(
    () => analytics.reorder(sc, { lead, safety, cover, multiplier }),
    [sc, lead, safety, cover, multiplier],
  )
  const sm = data.data?.summary

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (data.data?.rows ?? []).filter(
      (r) =>
        (status === 'all' || r.status === status) &&
        (!q || `${r.sku} ${r.product_name ?? ''}`.toLowerCase().includes(q)),
    )
  }, [data.data, status, search])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold">Reposición e inventario parado</h1>
        <div className="flex flex-wrap items-end gap-4">
          <NumberField label="Tiempo de entrega" value={lead} min={1} max={365} suffix="días" onCommit={setLead} />
          <NumberField label="Colchón" value={safety} min={0} max={180} suffix="días" onCommit={setSafety} />
          <NumberField label="Cubrir después" value={cover} min={7} max={365} suffix="días" onCommit={setCover} />
          <NumberField
            label="Temporada"
            value={multiplier}
            min={0.1}
            max={10}
            step={0.1}
            suffix="× demanda"
            onCommit={setMultiplier}
          />
        </div>
      </div>

      <ChannelOnlyNotice channels={['walmart']} what="La reposición" />
      <ErrorAlert message={data.error} />

      {(sm?.late ?? 0) > 0 && (
        <Callout tone="danger">
          <strong>
            {count(sm?.late ?? 0)} productos se agotarán antes de que llegue un pedido hecho hoy
          </strong>{' '}
          (entrega de {lead} días). Pedir cuesta unos {money(sm?.order_cost ?? 0)} por {count(sm?.units_to_order ?? 0)}{' '}
          unidades.
        </Callout>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Por pedir"
          value={count((sm?.late ?? 0) + (sm?.order_now ?? 0))}
          hint={`${count(sm?.units_to_order ?? 0)} unidades · ${money(sm?.order_cost ?? 0)} a costo`}
          className={(sm?.late ?? 0) > 0 ? 'text-red-600' : ''}
        />
        <Kpi title="Capital en inventario" value={money(sm?.capital_in_stock ?? 0)} hint="existencias al costo" />
        <Kpi
          title="Capital en exceso"
          value={money(sm?.capital_excess ?? 0)}
          hint="más de lo que cubren la entrega, el colchón y los días a cubrir"
          className={(sm?.capital_excess ?? 0) > 0 ? 'text-amber-600' : ''}
        />
        <Kpi
          title="Capital sin ventas"
          value={money(sm?.capital_dead ?? 0)}
          hint={`${count(sm?.dead ?? 0)} productos con stock y 0 ventas`}
          className={(sm?.capital_dead ?? 0) > 0 ? 'text-amber-600' : ''}
        />
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <Input
          placeholder="Buscar por SKU o nombre"
          aria-label="Buscar producto"
          className="w-64"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Segmented
          label="Estado"
          value={status}
          options={[
            { value: 'all', label: 'Todos' },
            { value: 'late', label: 'Pedir ya', count: sm?.late },
            { value: 'order_now', label: 'Pedir ahora', count: sm?.order_now },
            { value: 'ok', label: 'Bien', count: sm?.ok },
            { value: 'overstock', label: 'Exceso', count: sm?.overstock },
            { value: 'dead', label: 'Sin ventas', count: sm?.dead },
          ]}
          onChange={setStatus}
        />
      </div>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Producto</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead className="text-right">Disponible</TableHead>
                <TableHead className="text-right">Venta diaria</TableHead>
                <TableHead className="text-right">Días de inventario</TableHead>
                <TableHead className="text-right">Pedir</TableHead>
                <TableHead>Pedir antes del</TableHead>
                <TableHead className="text-right">Valor en stock</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : 'Sin productos. Sincroniza Walmart desde Importar.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r) => (
                <TableRow key={`${r.account}-${r.sku}`}>
                  <TableCell>
                    <div className="flex min-w-48 flex-col">
                      <span className="font-medium">{r.product_name ?? r.sku}</span>
                      <span className="text-muted-foreground text-xs">
                        {r.sku} · {r.account}
                      </span>
                      {!r.has_cost && r.avail_to_sell > 0 && (
                        <span className="text-xs text-amber-700">sin costo cargado</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <Pill label={STATUS[r.status].label} className={STATUS[r.status].pill} />
                    {r.status === 'late' && r.days_late > 0 && (
                      <div className="text-muted-foreground mt-1 text-xs">{r.days_late} días tarde</div>
                    )}
                  </TableCell>
                  <TableCell className="text-right">{count(r.avail_to_sell)}</TableCell>
                  <TableCell className="text-right">{r.daily_units.toFixed(2)}</TableCell>
                  <TableCell className="text-right">{days1(r.days_of_cover)}</TableCell>
                  <TableCell className="text-right font-medium">{r.order_qty > 0 ? count(r.order_qty) : '—'}</TableCell>
                  <TableCell>{r.order_qty > 0 ? (r.order_by ?? '—') : '—'}</TableCell>
                  <TableCell className="text-right">{money(r.stock_value)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Hint>
        Venta diaria = unidades de los últimos {data.data?.params.window_days ?? 30} días entre los días, por el factor
        de temporada. Se pide cuando el inventario dura menos que el tiempo de entrega más el colchón, y la cantidad
        cubre entrega + colchón + días a cubrir menos lo que ya tienes. El tiempo de entrega no viene en los reportes de
        contenedores (solo la fecha de recepción), por eso es un ajuste manual; no se descuentan pedidos en tránsito.
        Hoy las existencias solo se conocen en Walmart.
      </Hint>
    </div>
  )
}
