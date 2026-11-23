import { useMemo, useState } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { CreatorDecisions } from '@/components/analytics/CreatorDecisions'
import { api, type Filters } from '@/lib/api'
import { useScope } from '@/lib/scope'
import { count, money, percent, PRESETS, presetRange, tone, type Preset } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const STATUS_LABEL: Record<string, string> = {
  COMPLETED: 'Completada',
  OPS_COMPLETED: 'Completada',
  SHIPPED: 'Enviada',
  CONTENT_PENDING: 'Esperando contenido',
  PENDING: 'Por aprobar',
  REJECT_CANCELLED: 'Rechazada',
  UNFULFILL_CANCELLED: 'No surtida',
  UNFULFILLABLE_CANCELLED: 'No surtible',
  WITHDRAW_CANCELLED: 'Retirada por el creador',
  OVERDUE_CANCELLED: 'Vencida',
}

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

export function Samples() {
  const [preset, setPreset] = useState<Preset>('90d')
  const [onlyNoSales, setOnlyNoSales] = useState(false)
  const scope = useScope()
  const filters = useMemo<Filters>(
    () => ({ ...presetRange(preset), channel: scope.channelParam, account: scope.account }),
    [preset, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.samples(filters), [filters])

  const sm = data.data?.summary
  const all = data.data?.creators ?? []
  const rows = onlyNoSales ? all.filter((c) => c.affiliate_orders === 0) : all

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Muestras a creadores (ROI de gifting)</h1>
        <Select items={PRESETS} value={preset} onValueChange={(v) => v && setPreset(v as Preset)}>
          <SelectTrigger className="w-44" aria-label="Periodo">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PRESETS.map((p) => (
              <SelectItem key={p.value} value={p.value}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <ChannelOnlyNotice channels={['tiktok_shop']} what="Las muestras a creadores" />

      {data.error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {data.error}
        </div>
      )}

      {sm && sm.cost_not_in_profit > 0 && (
        <div role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <strong>{money(sm.cost_not_in_profit)} de muestras no están en tu utilidad.</strong> Las órdenes de muestra
          valen $0 y TikTok no las liquida, así que su costo (producto y etiqueta) no entra al estado de resultados.
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Costo de las muestras"
          value={money(sm?.sample_cost ?? 0)}
          hint={`${count(sm?.sample_orders ?? 0)} envíos · ${count(sm?.sample_units ?? 0)} unidades`}
        />
        <Kpi
          title="Utilidad de sus ventas"
          value={money(sm?.affiliate_profit ?? 0)}
          hint="órdenes de afiliado desde su primera muestra"
          className={tone(sm?.affiliate_profit)}
        />
        <Kpi
          title="Resultado neto"
          value={money(sm?.net ?? 0)}
          hint={`ROI ${percent(sm?.roi)}`}
          className={tone(sm?.net)}
        />
        <Kpi
          title="Creadores sin ventas"
          value={`${count(sm?.creators_without_sales ?? 0)} de ${count(sm?.creators ?? 0)}`}
          hint="recibieron muestra y no han vendido"
          className={(sm?.creators_without_sales ?? 0) > 0 ? 'text-red-600' : ''}
        />
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={onlyNoSales} onChange={(e) => setOnlyNoSales(e.target.checked)} />
        Solo creadores sin ventas
      </label>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Creador</TableHead>
                <TableHead className="text-right">Muestras</TableHead>
                <TableHead className="text-right">Costo</TableHead>
                <TableHead className="text-right">Órdenes</TableHead>
                <TableHead className="text-right">Ventas (base)</TableHead>
                <TableHead className="text-right">Comisión</TableHead>
                <TableHead className="text-right">Utilidad</TableHead>
                <TableHead className="text-right">Neto</TableHead>
                <TableHead className="text-right">ROI</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : 'Sin muestras en este periodo. Sincroniza TikTok Shop desde Importar.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((c) => (
                <TableRow key={c.creator}>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-medium">@{c.creator}</span>
                      <span className="text-muted-foreground text-xs">desde {c.first_sample ?? '—'}</span>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">{count(c.sample_orders)}</TableCell>
                  <TableCell className="text-right">{money(c.sample_cost)}</TableCell>
                  <TableCell className="text-right">
                    {count(c.affiliate_orders)}
                    {c.orders_pending > 0 && (
                      <span className="text-muted-foreground text-xs" title="Órdenes aún sin liquidar: su utilidad no se puede calcular">
                        {' '}
                        (+{c.orders_pending})
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">{money(c.affiliate_gmv)}</TableCell>
                  <TableCell className="text-right">{money(c.commission)}</TableCell>
                  <TableCell className={`text-right ${tone(c.affiliate_profit)}`}>{money(c.affiliate_profit)}</TableCell>
                  <TableCell className={`text-right font-medium ${tone(c.net)}`}>{money(c.net)}</TableCell>
                  <TableCell className={`text-right ${tone(c.roi)}`}>{percent(c.roi)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Embudo de solicitudes de muestra</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="divide-y text-sm">
            {(data.data?.applications_by_status ?? []).map((s) => (
              <li key={s.status} className="flex items-center justify-between py-1.5">
                <span>{STATUS_LABEL[s.status] ?? s.status}</span>
                <span className="font-medium">{count(s.applications)}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      <CreatorDecisions />

      <p className="text-muted-foreground max-w-3xl text-xs">
        Costo de la muestra = costo del producto + etiqueta de envío. Utilidad del creador = utilidad neta de sus
        órdenes de afiliado desde su primera muestra; la comisión ya está descontada en esa utilidad. Entre paréntesis,
        las órdenes que aún no se liquidan (se suman cuando TikTok las liquide). Los productos sin costo registrado
        cuentan como $0, así que el costo real de las muestras es mayor al mostrado.
      </p>
    </div>
  )
}
