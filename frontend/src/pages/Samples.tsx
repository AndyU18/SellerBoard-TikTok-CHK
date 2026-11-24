import { useMemo, useState } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { BreakEvenBubble, type BreakEvenPoint } from '@/components/analytics/BreakEvenBubble'
import { ErrorAlert } from '@/components/analytics/ui'
import { ComboChart } from '@/components/charts/ComboChart'
import { ChartCard, EmptyChart } from '@/components/charts/kit'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { CreatorDecisions } from '@/components/analytics/CreatorDecisions'
import { api, type Filters } from '@/lib/api'
import { useScope } from '@/lib/scope'
import { count, money, percent, tone } from '@/lib/format'
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
  const [onlyNoSales, setOnlyNoSales] = useState(false)
  const [month, setMonth] = useState<string | null>(null)
  const [focus, setFocus] = useState<string | null>(null)
  const scope = useScope()
  const { start, end } = scope.range
  const filters = useMemo<Filters>(
    () => ({ start, end, channel: scope.channelParam, account: scope.account }),
    [start, end, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.samples(filters), [filters])

  const sm = data.data?.summary
  const all = useMemo(() => data.data?.creators ?? [], [data.data])
  const rows = all
    .filter((c) => !onlyNoSales || c.affiliate_orders === 0)
    .filter((c) => !month || (c.first_sample ?? '').slice(0, 7) === month)
    .filter((c) => !focus || c.creator === focus)

  // Cohortes: el backend no entrega serie diaria, así que se agrupa por el mes de la primera muestra de cada creador.
  const cohorts = useMemo(() => {
    const m = new Map<string, { month: string; cost: number; gmv: number; profit: number; creators: number }>()
    for (const c of all) {
      const k = (c.first_sample ?? '').slice(0, 7)
      if (!k) continue
      const r = m.get(k) ?? { month: k, cost: 0, gmv: 0, profit: 0, creators: 0 }
      r.cost += c.sample_cost
      r.gmv += c.affiliate_gmv
      r.profit += c.affiliate_profit
      r.creators += 1
      m.set(k, r)
    }
    return [...m.values()].sort((a, b) => a.month.localeCompare(b.month))
  }, [all])

  const points = useMemo<BreakEvenPoint[]>(
    () =>
      all.map((c) => ({
        id: c.creator,
        label: `@${c.creator}`,
        x: c.sample_cost,
        y: Math.max(0, c.affiliate_profit),
        z: Math.max(1, c.affiliate_orders),
        group: c.affiliate_orders === 0 ? 'none' : c.net >= 0 ? 'paid' : 'partial',
        extra: [
          { label: 'Ventas (base)', value: money(c.affiliate_gmv) },
          { label: 'Neto', value: money(c.net) },
        ],
      })),
    [all],
  )

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Muestras a creadores (ROI de gifting)</h1>

      <ChannelOnlyNotice channels={['tiktok_shop']} what="Las muestras a creadores" />

      <ErrorAlert message={data.error} />

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

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Costo de muestras vs ventas generadas"
          subtitle="Por mes de la primera muestra de cada creador: barras = costo enviado, línea = ventas (base de comisión) que esos creadores han generado. Clic en un mes para filtrar la tabla."
          csv={cohorts.map((c) => ({ mes: c.month, creadores: c.creators, costo_muestras: c.cost, ventas: c.gmv, utilidad: c.profit }))}
          filename="muestras-costo-vs-ventas"
          actions={
            month ? (
              <button type="button" onClick={() => setMonth(null)} className="rounded border px-2 py-0.5 text-xs hover:bg-slate-50">
                {month} · quitar
              </button>
            ) : null
          }
        >
          {cohorts.length === 0 ? (
            <EmptyChart>{data.loading ? 'Cargando…' : 'Sin muestras en este período.'}</EmptyChart>
          ) : (
            <ComboChart
              data={cohorts}
              xKey="month"
              onPointClick={(r) => setMonth(String(r.month))}
              series={[
                { key: 'cost', label: 'Costo de muestras', type: 'bar', color: '#ef4444' },
                { key: 'gmv', label: 'Ventas generadas', type: 'line', color: '#10b981' },
              ]}
            />
          )}
        </ChartCard>

        <ChartCard
          title="Costo de muestras vs utilidad por creador"
          subtitle="Tamaño = órdenes. Sobre la diagonal el creador ya cubrió su muestra. Clic en una burbuja para ver su fila."
          csv={all.map((c) => ({ creador: c.creator, costo_muestras: c.sample_cost, ordenes: c.affiliate_orders, ventas: c.affiliate_gmv, utilidad: c.affiliate_profit, neto: c.net }))}
          filename="muestras-por-creador"
          actions={
            focus ? (
              <button type="button" onClick={() => setFocus(null)} className="rounded border px-2 py-0.5 text-xs hover:bg-slate-50">
                @{focus} · quitar
              </button>
            ) : null
          }
        >
          {all.length === 0 ? (
            <EmptyChart>{data.loading ? 'Cargando…' : 'Sin muestras en este período.'}</EmptyChart>
          ) : (
            <BreakEvenBubble
              points={points}
              groups={{
                paid: { label: 'Ya cubrió su muestra', color: '#10b981' },
                partial: { label: 'Vende pero no cubre', color: '#f59e0b' },
                none: { label: 'Sin ventas', color: '#ef4444' },
              }}
              xLabel="Costo de muestras"
              yLabel="Utilidad generada"
              zLabel="Órdenes"
              format={money}
              onPointClick={(p) => setFocus(p.id)}
            />
          )}
        </ChartCard>
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
