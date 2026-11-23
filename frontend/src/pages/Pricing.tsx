import { useMemo, useState } from 'react'

import { ChannelBadge } from '@/components/ChannelBadge'
import { Callout, ErrorAlert, Hint, Kpi, NumberField, PILL, Pill, Segmented, pct1 } from '@/components/analytics/ui'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { CHANNEL_LABEL } from '@/lib/api'
import { analytics, type FloorStatus } from '@/lib/analytics'
import { count, money, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const STATUS: Record<FloorStatus, { label: string; pill: string }> = {
  loss: { label: 'Pierde dinero', pill: PILL.red },
  below_target: { label: 'Bajo la meta', pill: PILL.amber },
  ok: { label: 'En la meta', pill: PILL.green },
  headroom: { label: 'Margen de sobra', pill: PILL.blue },
  no_cost: { label: 'Sin costo', pill: PILL.slate },
  insufficient: { label: 'Pocas ventas', pill: PILL.slate },
}

const WINDOWS = [
  { value: '30', label: '30 días' },
  { value: '60', label: '60 días' },
  { value: '90', label: '90 días' },
]

export function Pricing() {
  const scope = useScope()
  const [tab, setTab] = useState<'floor' | 'parity'>('floor')
  const [days, setDays] = useState(60)
  const [margin, setMargin] = useState(0.15)
  const [status, setStatus] = useState<FloorStatus | 'all'>('all')
  const [search, setSearch] = useState('')

  const sc = useMemo(
    () => ({ channel: scope.channelParam, account: scope.account }),
    [scope.channelParam, scope.account],
  )
  const floor = useAsync(
    () => (tab === 'floor' ? analytics.priceFloor(sc, { days, targetMargin: margin }) : Promise.resolve(null)),
    [tab, sc, days, margin],
  )
  const parity = useAsync(() => (tab === 'parity' ? analytics.parity(days, 0.1) : Promise.resolve(null)), [tab, days])

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (floor.data?.rows ?? []).filter(
      (r) =>
        (status === 'all' || r.status === status) &&
        (!q || `${r.sku} ${r.product_name ?? ''}`.toLowerCase().includes(q)),
    )
  }, [floor.data, status, search])
  const sm = floor.data?.summary

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold">Precios</h1>
        <div className="flex flex-wrap items-end gap-4">
          <Segmented label="Ventana" value={String(days)} options={WINDOWS} onChange={(v) => setDays(Number(v))} />
          {tab === 'floor' && (
            <NumberField
              label="Margen meta"
              value={Math.round(margin * 100)}
              min={0}
              max={80}
              suffix="%"
              onCommit={(v) => setMargin(v / 100)}
            />
          )}
        </div>
      </div>

      <Segmented
        label="Sección"
        value={tab}
        options={[
          { value: 'floor', label: 'Piso rentable por producto' },
          { value: 'parity', label: 'Paridad entre canales' },
        ]}
        onChange={setTab}
      />

      {tab === 'floor' && (
        <>
          <ErrorAlert message={floor.error} />
          {(sm?.loss ?? 0) > 0 && (
            <Callout tone="danger">
              <strong>{count(sm?.loss ?? 0)} productos se venden por debajo de su precio de equilibrio.</strong> Cada
              unidad que venden pierde dinero con las comisiones, el envío y el costo de los últimos {days} días.
            </Callout>
          )}
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Kpi
              title="Pierden dinero"
              value={count(sm?.loss ?? 0)}
              hint="precio cobrado menor al equilibrio"
              className={(sm?.loss ?? 0) > 0 ? 'text-red-600' : ''}
            />
            <Kpi
              title="Bajo la meta"
              value={count(sm?.below_target ?? 0)}
              hint={`ganan menos de ${Math.round(margin * 100)}%`}
            />
            <Kpi title="Margen de sobra" value={count(sm?.headroom ?? 0)} hint="pueden bajar precio o subir descuento" />
            <Kpi
              title="Utilidad que dejas de ganar"
              value={money(sm?.profit_gap_total ?? 0)}
              hint="si todos llegaran a la meta, con las unidades vendidas"
              className={(sm?.profit_gap_total ?? 0) > 0 ? 'text-amber-600' : ''}
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
                { value: 'loss', label: 'Pierden', count: sm?.loss },
                { value: 'below_target', label: 'Bajo la meta', count: sm?.below_target },
                { value: 'ok', label: 'En la meta', count: sm?.ok },
                { value: 'headroom', label: 'Margen de sobra', count: sm?.headroom },
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
                    <TableHead className="text-right">Unid.</TableHead>
                    <TableHead className="text-right">Precio cobrado</TableHead>
                    <TableHead className="text-right">Equilibrio</TableHead>
                    <TableHead className="text-right">Precio meta</TableHead>
                    <TableHead className="text-right">Lista sugerida</TableHead>
                    <TableHead className="text-right">Descuento máx.</TableHead>
                    <TableHead className="text-right">Margen</TableHead>
                    <TableHead>Estado</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={9} className="text-muted-foreground py-10 text-center">
                        {floor.loading ? 'Cargando…' : 'Sin productos con este filtro.'}
                      </TableCell>
                    </TableRow>
                  )}
                  {rows.map((r) => (
                    <TableRow key={`${r.channel}-${r.sku}`}>
                      <TableCell>
                        <div className="flex min-w-56 flex-col">
                          <span className="font-medium">{r.product_name ?? r.sku}</span>
                          <span className="text-muted-foreground flex items-center gap-2 text-xs">
                            {r.sku} <ChannelBadge channel={r.channel} />
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="text-right">{count(r.units)}</TableCell>
                      <TableCell className="text-right">{money(r.realized_price)}</TableCell>
                      <TableCell className="text-right">{money(r.break_even_price)}</TableCell>
                      <TableCell className="text-right">{money(r.target_price)}</TableCell>
                      <TableCell className="text-right font-medium">{money(r.suggested_list_price)}</TableCell>
                      <TableCell className={`text-right ${tone(r.max_discount_break_even)}`}>
                        {pct1(r.max_discount_break_even)}
                      </TableCell>
                      <TableCell className={`text-right ${tone(r.margin)}`}>{pct1(r.margin)}</TableCell>
                      <TableCell>
                        <Pill label={STATUS[r.status].label} className={STATUS[r.status].pill} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <Hint>
            Precio cobrado = lo que realmente paga el cliente después de tus promociones. Equilibrio = precio con el que
            no ganas ni pierdes, con la comisión, los reembolsos, el envío y el costo reales de los últimos {days} días.
            Precio meta = el que deja el margen elegido. Lista sugerida = precio de lista que lo logra manteniendo tu
            promoción actual. Descuento máximo = cuánto puedes bajar la lista antes de llegar al equilibrio. Sin costo
            cargado o con menos de 3 unidades no se calcula un veredicto.
          </Hint>
        </>
      )}

      {tab === 'parity' && (
        <>
          <ErrorAlert message={parity.error} />
          <Callout tone="info">
            La paridad compara el mismo producto entre canales (se unen variantes como <code>-1PK</code>), así que ignora
            el filtro de canal de arriba.
          </Callout>
          <div className="grid gap-4 md:grid-cols-2">
            <Kpi title="Productos en 2 canales o más" value={count(parity.data?.summary.products ?? 0)} />
            <Kpi
              title="Con brecha de precio (10 % o más)"
              value={count(parity.data?.summary.with_gap ?? 0)}
              hint="mismo producto, precio muy distinto"
              className={(parity.data?.summary.with_gap ?? 0) > 0 ? 'text-amber-600' : ''}
            />
          </div>
          <Card>
            <CardContent className="overflow-x-auto pt-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Producto</TableHead>
                    <TableHead>Precio por canal</TableHead>
                    <TableHead>Utilidad por unidad</TableHead>
                    <TableHead className="text-right">Brecha</TableHead>
                    <TableHead>Conviene vender en</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(parity.data?.rows.length ?? 0) === 0 && (
                    <TableRow>
                      <TableCell colSpan={5} className="text-muted-foreground py-10 text-center">
                        {parity.loading ? 'Cargando…' : 'Ningún producto se vende todavía en dos canales.'}
                      </TableCell>
                    </TableRow>
                  )}
                  {(parity.data?.rows ?? []).map((r) => (
                    <TableRow key={r.base_sku}>
                      <TableCell>
                        <div className="flex min-w-48 flex-col">
                          <span className="font-medium">{r.product_name ?? r.base_sku}</span>
                          <span className="text-muted-foreground text-xs">{r.base_sku}</span>
                          {!r.complete_costs && <span className="text-xs text-amber-700">falta costo en algún canal</span>}
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-col gap-0.5 text-sm">
                          {r.channels.map((c) => (
                            <span key={c.channel}>
                              {CHANNEL_LABEL[c.channel]}: <strong>{money(c.realized_price)}</strong>
                              <span className="text-muted-foreground"> · {count(c.units)} unid.</span>
                            </span>
                          ))}
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-col gap-0.5 text-sm">
                          {r.channels.map((c) => (
                            <span key={c.channel} className={tone(c.unit_profit)}>
                              {CHANNEL_LABEL[c.channel]}: {money(c.unit_profit)}
                            </span>
                          ))}
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        <Pill label={pct1(r.price_gap)} className={r.flag === 'gap' ? PILL.amber : PILL.slate} />
                      </TableCell>
                      <TableCell>{CHANNEL_LABEL[r.best_profit_channel]}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <Hint>
            Brecha = diferencia entre el precio más alto y el más bajo que realmente se cobró. “Conviene vender en” es
            el canal con más utilidad por unidad en este periodo; no considera inventario disponible por canal.
          </Hint>
        </>
      )}
    </div>
  )
}
