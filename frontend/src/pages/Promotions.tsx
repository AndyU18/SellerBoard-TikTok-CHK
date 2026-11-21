import { useMemo, useState } from 'react'

import { ChannelBadge } from '@/components/ChannelBadge'
import { FilterBar, type ChannelFilter } from '@/components/FilterBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, type Filters } from '@/lib/api'
import { count, money, percent, presetRange, tone, type Preset } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

function Kpi({ title, value, hint }: { title: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold">{value}</div>
        {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
      </CardContent>
    </Card>
  )
}

export function Promotions() {
  const [preset, setPreset] = useState<Preset>('90d')
  const [channel, setChannel] = useState<ChannelFilter>('all')
  const [onlyAlerts, setOnlyAlerts] = useState(false)

  const filters = useMemo<Filters>(
    () => ({ ...presetRange(preset), channel: channel === 'all' ? '' : channel }),
    [preset, channel],
  )
  const data = useAsync(() => api.promotions(filters), [filters])

  const totals = data.data?.totals ?? null
  const all = data.data?.rows ?? []
  const rows = onlyAlerts ? all.filter((r) => r.loses_money || r.deep_discount) : all
  const losers = all.filter((r) => r.loses_money).length
  const unknownList = all.some((r) => r.list_amount === 0 && r.promo > 0)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Auditoría de promociones y subsidios</h1>
        <FilterBar preset={preset} onPreset={setPreset} channel={channel} onChannel={setChannel} />
      </div>

      {data.error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {data.error}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Descuento del vendedor"
          value={money(totals?.promo ?? 0)}
          hint={`${percent(totals?.promo_rate)} de las ventas · resta de tu utilidad`}
        />
        <Kpi
          title="Subsidio de la plataforma"
          value={money(totals?.platform_discount ?? 0)}
          hint={`${percent(totals?.subsidy_share)} del descuento total · no resta utilidad`}
        />
        <Kpi
          title="Profundidad del descuento"
          value={percent(totals?.discount_depth)}
          hint="(vendedor + plataforma) / precio de lista"
        />
        <Kpi
          title="Productos con promo que pierden"
          value={String(losers)}
          hint="utilidad negativa y descuento del vendedor"
        />
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={onlyAlerts} onChange={(e) => setOnlyAlerts(e.target.checked)} />
        Solo con alertas (pierden dinero o descuento ≥ 30%)
      </label>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Producto</TableHead>
                <TableHead>Canal</TableHead>
                <TableHead className="text-right">Unid.</TableHead>
                <TableHead className="text-right">Ventas</TableHead>
                <TableHead className="text-right">Desc. vendedor</TableHead>
                <TableHead className="text-right">% de ventas</TableHead>
                <TableHead className="text-right">Subsidio plataforma</TableHead>
                <TableHead className="text-right">Profundidad</TableHead>
                <TableHead className="text-right">Utilidad neta</TableHead>
                <TableHead>Alerta</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={10} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : 'Sin datos para este filtro.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r) => (
                <TableRow key={`${r.channel}-${r.sku}`}>
                  <TableCell>
                    <div className="flex min-w-48 flex-col">
                      <span className="font-medium">{r.product_name ?? r.sku}</span>
                      <span className="text-muted-foreground text-xs">{r.sku}</span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <ChannelBadge channel={r.channel} />
                  </TableCell>
                  <TableCell className="text-right">{count(r.units)}</TableCell>
                  <TableCell className="text-right">{money(r.gross_sales)}</TableCell>
                  <TableCell className="text-right">{money(r.promo)}</TableCell>
                  <TableCell className="text-right">{percent(r.promo_rate)}</TableCell>
                  <TableCell className="text-right">{money(r.platform_discount)}</TableCell>
                  <TableCell className="text-right">{percent(r.discount_depth)}</TableCell>
                  <TableCell className={`text-right font-medium ${tone(r.net_profit)}`}>
                    {money(r.net_profit)}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {r.loses_money && (
                        <span className="rounded bg-red-100 px-2 py-0.5 text-xs text-red-800">Pierde dinero</span>
                      )}
                      {r.deep_discount && (
                        <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
                          Descuento agresivo
                        </span>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <p className="text-muted-foreground max-w-3xl text-xs">
        El subsidio y el precio de lista solo los informa TikTok Shop, y solo en las órdenes que se sincronizaron
        después de esta versión; en Walmart y en los CSV aparecen como —. Para completar el histórico vuelve a
        sincronizar TikTok desde Importar.
        {unknownList && ' Hay productos con descuento sin precio de lista: su profundidad no se puede calcular.'}
      </p>
    </div>
  )
}
