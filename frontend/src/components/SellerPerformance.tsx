import { useMemo } from 'react'
import { ComboChart } from '@/components/charts/ComboChart'
import { ChartCard } from '@/components/charts/kit'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, type Filters, type PerformanceWindow } from '@/lib/api'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const pct = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)}%`)

type Metric = {
  key: keyof Omit<PerformanceWindow, 'window_days'>
  label: string
  /** 'min': debe estar sobre la meta; 'max': debe estar bajo la meta; sin meta: solo informativo. */
  goal?: { kind: 'min' | 'max'; target: string }
}

const METRICS: Metric[] = [
  { key: 'ots', label: 'Enviado a tiempo', goal: { kind: 'min', target: 'ots' } },
  { key: 'otd', label: 'Entregado a tiempo', goal: { kind: 'min', target: 'otd' } },
  { key: 'vtr', label: 'Guía válida', goal: { kind: 'min', target: 'vtr' } },
  { key: 'refund_rate_seller_fault', label: 'Reembolsos (tu culpa)' },
  { key: 'refund_rate_customer_fault', label: 'Reembolsos (cliente)' },
  { key: 'cancellation_seller_fault', label: 'Cancelaciones (tu culpa)', goal: { kind: 'max', target: 'cancellation_seller_fault' } },
  { key: 'cancellation_customer_fault', label: 'Cancelaciones (cliente)' },
]

export function SellerPerformance() {
  const scope = useScope()
  const filters = useMemo<Filters>(
    () => ({ channel: scope.channelParam, account: scope.account }) as Filters,
    [scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.sellerPerformance(filters), [filters])
  const targets = data.data?.targets ?? {}
  const accounts = data.data?.accounts ?? []
  if (data.error) {
    return (
      <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
        No se pudo cargar el desempeño del vendedor: {data.error}
      </div>
    )
  }
  if (accounts.length === 0) return null

  const bad = (m: Metric, v: number | null) => {
    if (v == null || !m.goal) return false
    const target = targets[m.goal.target]
    if (target == null) return false
    return m.goal.kind === 'min' ? v < target : v > target
  }

  return (
    <>
      {accounts.map((a) => {
        const history = (data.data?.history ?? []).filter((h) => h.account === a.account)
        return (
          <Card key={a.account}>
            <CardHeader>
              <CardTitle>
                Desempeño del vendedor · {a.account}
                <span className="text-muted-foreground ml-2 text-xs font-normal">al {a.snapshot_date}</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Ventana</TableHead>
                    {METRICS.map((m) => (
                      <TableHead key={m.key} className="text-right">
                        {m.label}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {a.windows.map((w) => (
                    <TableRow key={w.window_days}>
                      <TableCell className="font-medium">{w.window_days} días</TableCell>
                      {METRICS.map((m) => (
                        <TableCell
                          key={m.key}
                          className={`text-right ${bad(m, w[m.key]) ? 'font-medium text-red-600' : ''}`}
                        >
                          {pct(w[m.key])}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {history.length >= 2 && (
                <ChartCard
                  title="Historial del desempeño"
                  subtitle="Toca la leyenda para encender o apagar cada indicador."
                  csv={history.map((h) => ({ fecha: h.snapshot_date, entregado_a_tiempo: h.otd, enviado_a_tiempo: h.ots, guia_valida: h.vtr }))}
                  filename={`desempeno-${a.account}`}
                >
                  <ComboChart
                    data={history}
                    xKey="snapshot_date"
                    height={240}
                    leftFormat={(v) => `${v}%`}
                    series={[
                      { key: 'otd', label: 'Entregado a tiempo', type: 'line', color: '#2563eb', format: pct },
                      { key: 'ots', label: 'Enviado a tiempo', type: 'line', color: '#16a34a', format: pct },
                      { key: 'vtr', label: 'Guía válida', type: 'line', color: '#f59e0b', format: pct },
                    ]}
                  />
                </ChartCard>
              )}

              <p className="text-muted-foreground text-xs">
                En rojo, lo que está fuera de la meta de referencia (entrega a tiempo {pct(targets.otd)}, envío a tiempo{' '}
                {pct(targets.ots)}, guía válida {pct(targets.vtr)}, cancelaciones tuyas máximo{' '}
                {pct(targets.cancellation_seller_fault)}). Confirma las metas vigentes en el Seller Center. La gráfica
                aparece cuando hay al menos dos días de historial: la app guarda una foto en cada sincronización.
              </p>
            </CardContent>
          </Card>
        )
      })}
    </>
  )
}
