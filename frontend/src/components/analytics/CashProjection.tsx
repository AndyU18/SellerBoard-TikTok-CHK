import { useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { Callout, ErrorAlert, Hint, Kpi } from '@/components/analytics/ui'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { analytics } from '@/lib/analytics'
import { money } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const HORIZON = 14

/** Dinero que se espera recibir en los próximos días y comparación con la utilidad. */
export function CashProjection() {
  const scope = useScope()
  const sc = useMemo(
    () => ({ channel: scope.channelParam, account: scope.account }),
    [scope.channelParam, scope.account],
  )
  const data = useAsync(() => analytics.cashProjection(sc, HORIZON), [sc])
  const d = data.data
  const sm = d?.summary
  const chart = (d?.series ?? []).map((p) => ({
    date: p.date.slice(5),
    'Ya liquidado': Math.round(p.known),
    'Por el ritmo reciente': Math.round(p.projected),
  }))

  return (
    <section className="space-y-4">
      <h2 className="text-xl font-semibold">Qué entra en los próximos {HORIZON} días</h2>
      <ErrorAlert message={data.error} />
      {d?.note && <Callout tone="info">{d.note}</Callout>}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title={`Esperado en ${HORIZON} días`}
          value={money(sm?.expected_in_horizon ?? 0)}
          hint={`${money(sm?.known_in_horizon ?? 0)} ya liquidado + ${money(sm?.projected_in_horizon ?? 0)} por el ritmo reciente`}
          className="text-green-700"
        />
        <Kpi title="Por cobrar hoy" value={money(sm?.pending_total ?? 0)} hint="estados de cuenta en proceso" />
        <Kpi
          title="Rezago de pago"
          value={`${d?.lag_days ?? '—'} días`}
          hint={d?.lag_measured ? 'mediana medida en tus pagos' : 'estimado: aún no hay pagos de los que medir'}
        />
        <Kpi
          title="Recibido en 30 días"
          value={money(sm?.received_30d ?? 0)}
          hint={`la utilidad de esos canales fue ${money(sm?.profit_30d ?? 0)}`}
        />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Depósitos esperados por día</CardTitle>
        </CardHeader>
        <CardContent className="h-64">
          {chart.length === 0 ? (
            <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
              {data.loading ? 'Cargando…' : 'Sin proyección.'}
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chart}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" />
                <YAxis tickFormatter={(v: number) => `$${v.toLocaleString('en-US')}`} width={72} />
                <Tooltip formatter={(v) => money(Number(v))} />
                <Legend />
                <Bar dataKey="Ya liquidado" stackId="cash" fill="#16a34a" />
                <Bar dataKey="Por el ritmo reciente" stackId="cash" fill="#86efac" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>
      <Hint>
        “Ya liquidado” son estados de cuenta cerrados que aún no se depositan, en su fecha de pago. “Por el ritmo
        reciente” supone que lo que se liquidó por día en las últimas 4 semanas sigue igual y se paga con el mismo
        rezago. Lo recibido es mayor que la utilidad porque la plataforma no descuenta el costo de tu producto. Solo
        cubre canales que informan liquidaciones ({d?.channels_covered.join(', ') || 'ninguno por ahora'}).
      </Hint>
    </section>
  )
}
