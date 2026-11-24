import { useMemo } from 'react'

import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { ErrorAlert } from '@/components/analytics/ui'
import { Donut } from '@/components/charts/Donut'
import { ChartCard } from '@/components/charts/kit'
import { AsyncBody, HBars } from '@/components/operacion/shared'
import { SellerPerformance } from '@/components/SellerPerformance'
import { ShippingProfit } from '@/components/analytics/ShippingProfit'
import { useScope } from '@/lib/scope'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, type Filters } from '@/lib/api'
import { count, percent } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const STATUS_LABEL: Record<string, string> = {
  DELIVERED: 'Entregada',
  COMPLETED: 'Completada',
  IN_TRANSIT: 'En tránsito',
  CANCELLED: 'Cancelada',
  AWAITING_SHIPMENT: 'Por enviar',
  AWAITING_COLLECTION: 'Esperando recolección',
  ON_HOLD: 'En espera',
}

const hours = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)} h`)
const days = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)} d`)

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

export function Logistics() {
  const scope = useScope()
  const { start, end } = scope.range
  const filters = useMemo<Filters>(
    () => ({ start, end, channel: scope.channelParam, account: scope.account }),
    [start, end, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.logistics(filters), [filters])
  const sm = data.data?.summary
  const carriers = data.data?.by_carrier ?? []
  const statusSlices = (data.data?.by_status ?? []).map((s) => ({ id: s.status, label: STATUS_LABEL[s.status] ?? s.status, value: s.orders }))

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Logística y entregas</h1>
      </div>
      <ChannelOnlyNotice channels={['tiktok_shop']} what="Los tiempos de envío y transportistas" />
      <SellerPerformance />

      <ErrorAlert message={data.error} />

      {(sm?.overdue_now ?? 0) > 0 && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          <strong>{count(sm?.overdue_now ?? 0)} órdenes por enviar ya pasaron su fecha límite.</strong> La plataforma
          puede cancelarlas y penalizarte.
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Tiempo de manejo"
          value={hours(sm?.handling_hours_median)}
          hint={`mediana, de pago a listo para enviar · promedio ${hours(sm?.handling_hours_avg)}`}
        />
        <Kpi
          title="Tiempo en tránsito"
          value={days(sm?.transit_days_median)}
          hint={`mediana, de recolección a entrega · promedio ${days(sm?.transit_days_avg)}`}
        />
        <Kpi
          title="Envíos a tiempo"
          value={percent(sm?.on_time_rate)}
          hint={`${count(sm?.shipped_on_time ?? 0)} a tiempo · ${count(sm?.shipped_late ?? 0)} tarde`}
          className={(sm?.shipped_late ?? 0) > 0 ? 'text-red-600' : ''}
        />
        <Kpi
          title="Órdenes de muestra"
          value={count(sm?.sample_orders ?? 0)}
          hint={`de ${count(sm?.shipments ?? 0)} órdenes en el periodo`}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Tránsito promedio por transportista"
          subtitle="Días de la recolección a la entrega; al pasar el cursor se ven los envíos."
          csv={carriers.map((c) => ({ transportista: c.carrier, envios: c.shipments, entregados: c.delivered, transito_dias: c.transit_days_avg }))}
          filename="transito-por-transportista"
        >
          <AsyncBody state={data} empty={carriers.length === 0}>
            <HBars
              data={carriers.map((c) => ({ id: c.carrier, label: c.carrier, value: c.transit_days_avg ?? 0, extra: `${count(c.shipments)} envíos · ${count(c.delivered)} entregados` }))}
              format={(v) => `${v.toFixed(1)} d`}
              valueLabel="Tránsito"
            />
          </AsyncBody>
        </ChartCard>
        <ChartCard
          title="Estado de las órdenes"
          csv={statusSlices.map((s) => ({ estado: s.label, ordenes: s.value }))}
          filename="estado-de-ordenes"
        >
          <AsyncBody state={data} empty={statusSlices.length === 0}>
            <Donut slices={statusSlices} format={count} centerLabel="órdenes" />
          </AsyncBody>
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Transportistas</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Transportista</TableHead>
                  <TableHead className="text-right">Envíos</TableHead>
                  <TableHead className="text-right">Entregados</TableHead>
                  <TableHead className="text-right">Tránsito prom.</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data.data?.by_carrier ?? []).map((c) => (
                  <TableRow key={c.carrier}>
                    <TableCell>{c.carrier}</TableCell>
                    <TableCell className="text-right">{count(c.shipments)}</TableCell>
                    <TableCell className="text-right">{count(c.delivered)}</TableCell>
                    <TableCell className="text-right">{days(c.transit_days_avg)}</TableCell>
                  </TableRow>
                ))}
                {(data.data?.by_carrier.length ?? 0) === 0 && (
                  <TableRow>
                    <TableCell colSpan={4} className="text-muted-foreground py-8 text-center">
                      {data.loading ? 'Cargando…' : 'Sin envíos. Sincroniza TikTok Shop desde Importar.'}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

      </div>

      <ShippingProfit />

      <p className="text-muted-foreground max-w-3xl text-xs">
        Los tiempos de envío y transportistas solo los informa TikTok Shop. Para Walmart se muestra su desempeño de
        entregas (arriba), que la propia plataforma calcula. “A tiempo” compara el momento en que la orden quedó lista para enviar con la fecha límite de la plataforma. Las
        órdenes de muestra (regalos a creadores) se cuentan aparte para la sección de Muestras.
      </p>
    </div>
  )
}
