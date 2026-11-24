import { useMemo, useState } from 'react'

import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { Callout, ErrorAlert, Hint, Kpi, NumberField, PILL, Pill, Segmented, days1 } from '@/components/analytics/ui'
import { ChartCard } from '@/components/charts/kit'
import { DepletionChart } from '@/components/operacion/DepletionChart'
import { AsyncBody, ColorDonut, HBars, SEMAFORO, type HBarItem } from '@/components/operacion/shared'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { analytics, type ReorderStatus } from '@/lib/analytics'
import { count, money } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const STATUS_COLOR: Record<ReorderStatus, string> = {
  late: '#ef4444',
  order_now: '#f59e0b',
  ok: '#10b981',
  overstock: '#8b5cf6',
  dead: '#94a3b8',
  no_demand: '#cbd5e1',
}
const rowKey = (r: { account: string; sku: string }) => `${r.account}|${r.sku}`

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
  const [picked, setPicked] = useState<string | null>(null)

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

  const all = useMemo(() => data.data?.rows ?? [], [data.data])
  // Días de inventario por SKU, de menos a más; rojo si no alcanzan hasta que llegue un pedido hecho hoy.
  const coverRows = useMemo(
    () =>
      all
        .filter((r) => r.days_of_cover != null && r.daily_units > 0)
        .sort((a, b) => (a.days_of_cover ?? 0) - (b.days_of_cover ?? 0))
        .slice(0, 20),
    [all],
  )
  const coverBars: HBarItem[] = coverRows.map((r) => {
    const d = r.days_of_cover ?? 0
    return {
      id: rowKey(r),
      label: r.product_name ?? r.sku,
      value: Math.min(d, 180),
      color: d < lead ? SEMAFORO.red : d < lead + safety ? SEMAFORO.amber : SEMAFORO.green,
      extra: `${d.toFixed(0)} días · ${r.sku}`,
    }
  })
  const selectedRow = all.find((r) => rowKey(r) === picked) ?? coverRows[0]
  const statusSlices = (Object.keys(STATUS) as ReorderStatus[]).map((k) => ({
    id: k,
    label: STATUS[k].label,
    value: sm?.[k] ?? 0,
    color: STATUS_COLOR[k],
  }))
  const healthy = Math.max(0, (sm?.capital_in_stock ?? 0) - (sm?.capital_excess ?? 0) - (sm?.capital_dead ?? 0))
  const capitalSlices = [
    { id: 'ok', label: 'Stock sano', value: healthy, color: '#10b981' },
    { id: 'overstock', label: 'Exceso', value: sm?.capital_excess ?? 0, color: '#f59e0b' },
    { id: 'dead', label: 'Sin ventas', value: sm?.capital_dead ?? 0, color: '#ef4444' },
  ]
  const pick = (id: string) => {
    const next = picked === id ? null : id
    setPicked(next)
    const row = all.find((r) => rowKey(r) === next)
    setSearch(row ? row.sku : '')
  }

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

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Días de inventario por SKU"
          subtitle="Los que menos duran, primero. Clic en una barra para ver su proyección y filtrar la tabla."
          csv={coverRows.map((r) => ({ sku: r.sku, producto: r.product_name, tienda: r.account, disponible: r.avail_to_sell, venta_diaria: r.daily_units, dias_de_inventario: r.days_of_cover, pedir: r.order_qty, pedir_antes_del: r.order_by }))}
          filename="reposicion-dias-de-inventario"
        >
          <AsyncBody state={data} empty={coverBars.length === 0}>
            <HBars
              data={coverBars}
              format={(v) => `${v.toFixed(0)} d`}
              valueLabel="Días de inventario"
              selected={selectedRow ? rowKey(selectedRow) : null}
              onBarClick={(b) => pick(b.id)}
              legend={[
                { color: SEMAFORO.red, label: `Menos de ${lead} días (entrega)` },
                { color: SEMAFORO.amber, label: `Menos de ${lead + safety} días (entrega + colchón)` },
                { color: SEMAFORO.green, label: 'Alcanza' },
              ]}
            />
          </AsyncBody>
        </ChartCard>
        {selectedRow ? (
          <DepletionChart
            name={selectedRow.product_name ?? selectedRow.sku}
            sku={selectedRow.sku}
            stock={selectedRow.avail_to_sell}
            daily={selectedRow.daily_units}
            orderBy={selectedRow.order_qty > 0 ? selectedRow.order_by : null}
            leadDays={lead}
            reorderPoint={selectedRow.reorder_point}
          />
        ) : (
          <ChartCard title="Proyección de agotamiento" filename="proyeccion">
            <AsyncBody state={data} empty>
              {null}
            </AsyncBody>
          </ChartCard>
        )}
        <ChartCard
          title="Productos por estado de reposición"
          subtitle="Clic en una porción para filtrar la tabla."
          csv={statusSlices.map((s) => ({ estado: s.label, productos: s.value }))}
          filename="reposicion-por-estado"
        >
          <AsyncBody state={data} empty={statusSlices.every((s) => s.value === 0)}>
            <ColorDonut
              slices={statusSlices}
              format={count}
              centerLabel="productos"
              selected={status === 'all' ? null : status}
              onSliceClick={(s) => setStatus(status === s.id ? 'all' : (s.id as ReorderStatus))}
            />
          </AsyncBody>
        </ChartCard>
        <ChartCard
          title="Capital inmovilizado"
          subtitle="Existencias al costo: sano, en exceso y sin ventas."
          csv={capitalSlices.map((s) => ({ concepto: s.label, capital: Math.round(s.value * 100) / 100 }))}
          filename="capital-inmovilizado"
        >
          <AsyncBody state={data} empty={capitalSlices.every((s) => s.value === 0)}>
            <ColorDonut
              slices={capitalSlices}
              format={money}
              centerLabel="al costo"
              selected={status === 'ok' || status === 'overstock' || status === 'dead' ? status : null}
              onSliceClick={(s) => setStatus(status === s.id ? 'all' : (s.id as ReorderStatus))}
            />
          </AsyncBody>
        </ChartCard>
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
