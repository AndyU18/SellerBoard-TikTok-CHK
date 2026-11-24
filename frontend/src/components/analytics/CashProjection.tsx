import { useMemo, useState } from 'react'
import { Area, Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { Callout, ErrorAlert, Hint, Kpi } from '@/components/analytics/ui'
import { axisMoney, ChartCard, EmptyChart, TipBox } from '@/components/charts/kit'
import { analytics } from '@/lib/analytics'
import { shortDate } from '@/lib/chartdata_dinero'
import { money } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const HORIZON = 14

const SERIES = [
  { key: 'known', label: 'Ya liquidado (por día)', color: '#16a34a', kind: 'bar' },
  { key: 'projected', label: 'Por el ritmo reciente (por día)', color: '#86efac', kind: 'bar' },
  { key: 'cumKnown', label: 'Acumulado ya liquidado', color: '#3b82f6', kind: 'area' },
  { key: 'cumExpected', label: 'Acumulado esperado (base)', color: '#f59e0b', kind: 'line' },
] as const

/**
 * Dinero que se espera recibir en los próximos días: barras diarias, saldo acumulado (línea) contra lo ya
 * liquidado (área) y marcador de la próxima fecha de liquidación (`payoutDate`).
 * El backend entrega un solo escenario (base), por eso no hay banda optimista/pesimista.
 */
export function CashProjection({ payoutDate }: { payoutDate?: string | null }) {
  const scope = useScope()
  const sc = useMemo(
    () => ({ channel: scope.channelParam, account: scope.account }),
    [scope.channelParam, scope.account],
  )
  const data = useAsync(() => analytics.cashProjection(sc, HORIZON), [sc])
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const d = data.data
  const sm = d?.summary
  const chart = useMemo(
    () =>
      (d?.series ?? []).map((p, i, all) => ({
        date: p.date,
        known: Math.round(p.known),
        projected: Math.round(p.projected),
        cumKnown: Math.round(all.slice(0, i + 1).reduce((acc, x) => acc + x.known, 0)),
        cumExpected: Math.round(p.cumulative),
      })),
    [d],
  )
  const marker = payoutDate && chart.some((r) => r.date === payoutDate) ? payoutDate : null
  const toggle = (k: string) =>
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  const label = (k: string) => SERIES.find((s) => s.key === k)?.label ?? k

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
      <ChartCard
        title="Saldo proyectado y depósitos esperados"
        subtitle={`Barras: depósitos por día. Línea: acumulado esperado (eje derecho) contra lo ya liquidado (área).${marker ? ' La línea punteada marca la próxima liquidación.' : ''}`}
        csv={chart.map((r) => ({
          fecha: r.date,
          ya_liquidado: r.known,
          por_ritmo_reciente: r.projected,
          acumulado_liquidado: r.cumKnown,
          acumulado_esperado: r.cumExpected,
        }))}
        filename="flujo-de-caja-proyeccion"
      >
        {chart.length === 0 ? (
          <EmptyChart>{data.loading ? 'Cargando…' : 'Sin proyección.'}</EmptyChart>
        ) : (
          <div>
            <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
              {SERIES.map((s) => (
                <button key={s.key} type="button" aria-pressed={!hidden.has(s.key)} onClick={() => toggle(s.key)} className={`flex items-center gap-1.5 ${hidden.has(s.key) ? 'opacity-40' : ''}`}>
                  <span className={`inline-block size-3 ${s.kind === 'bar' ? 'rounded-sm' : 'rounded-full'}`} style={{ background: s.color }} />
                  {s.label}
                </button>
              ))}
            </div>
            <ResponsiveContainer width="100%" height={320}>
              <ComposedChart data={chart} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: 11 }} />
                <YAxis yAxisId="left" tickFormatter={axisMoney} tick={{ fontSize: 11 }} width={56} />
                <YAxis yAxisId="right" orientation="right" tickFormatter={axisMoney} tick={{ fontSize: 11 }} width={56} />
                <Tooltip
                  content={({ active, payload, label: l }) =>
                    active && payload?.length ? (
                      <TipBox
                        title={String(l)}
                        rows={payload.map((p) => ({ label: p.name ?? label(String(p.dataKey)), value: money(Number(p.value)), color: String(p.color ?? p.stroke ?? '') }))}
                      />
                    ) : null
                  }
                />
                {!hidden.has('known') && <Bar yAxisId="left" dataKey="known" name={label('known')} stackId="day" fill="#16a34a" />}
                {!hidden.has('projected') && <Bar yAxisId="left" dataKey="projected" name={label('projected')} stackId="day" fill="#86efac" />}
                {!hidden.has('cumKnown') && <Area yAxisId="right" type="monotone" dataKey="cumKnown" name={label('cumKnown')} stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.15} />}
                {!hidden.has('cumExpected') && (
                  <Line yAxisId="right" type="monotone" dataKey="cumExpected" name={label('cumExpected')} stroke="#f59e0b" strokeWidth={2} dot={{ r: 3, fill: '#fff', strokeWidth: 2 }} />
                )}
                {marker && (
                  <ReferenceLine yAxisId="left" x={marker} stroke="#64748b" strokeDasharray="4 4" label={{ value: 'Próxima liquidación', position: 'insideTopLeft', fontSize: 11, fill: '#475569' }} />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </ChartCard>
      <Hint>
        “Ya liquidado” son estados de cuenta cerrados que aún no se depositan, en su fecha de pago. “Por el ritmo
        reciente” supone que lo que se liquidó por día en las últimas 4 semanas sigue igual y se paga con el mismo
        rezago. Lo recibido es mayor que la utilidad porque la plataforma no descuenta el costo de tu producto. Solo
        cubre canales que informan liquidaciones ({d?.channels_covered.join(', ') || 'ninguno por ahora'}).
      </Hint>
    </section>
  )
}
