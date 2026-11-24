import { useMemo, useState } from 'react'

import { BreakEvenBubble, type BreakEvenPoint } from '@/components/analytics/BreakEvenBubble'
import { ErrorAlert, Hint, Kpi, PILL, Pill, Segmented, pct1 } from '@/components/analytics/ui'
import { ChartCard, EmptyChart } from '@/components/charts/kit'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { analytics, type CreatorAction } from '@/lib/analytics'
import { count, money, percent, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const ACTION: Record<CreatorAction, { label: string; pill: string }> = {
  repeat: { label: 'Repetir', pill: PILL.green },
  keep: { label: 'Observar', pill: PILL.blue },
  wait: { label: 'Esperar', pill: PILL.amber },
  cut: { label: 'Dejar de enviar', pill: PILL.red },
}

const ACTION_COLOR: Record<CreatorAction, string> = { repeat: '#10b981', keep: '#3b82f6', wait: '#f59e0b', cut: '#ef4444' }

const ORDER: CreatorAction[] = ['repeat', 'keep', 'wait', 'cut']

/** Qué creadores repetir, esperar, observar o dejar de enviar. */
export function CreatorDecisions() {
  const scope = useScope()
  const [action, setAction] = useState<CreatorAction | 'all'>('cut')
  const sc = useMemo(
    () => ({ channel: scope.channelParam, account: scope.account }),
    [scope.channelParam, scope.account],
  )
  const data = useAsync(() => analytics.creators(sc), [sc])
  const d = data.data
  const [focus, setFocus] = useState<string | null>(null)
  const rows = (d?.creators ?? []).filter((c) => (action === 'all' || c.action === action) && (!focus || c.creator === focus))
  const points = useMemo<BreakEvenPoint[]>(
    () =>
      (d?.creators ?? []).map((c) => ({
        id: c.creator,
        label: `@${c.creator} · ${ACTION[c.action].label}`,
        x: c.sample_cost,
        y: Math.max(0, c.profit),
        z: Math.max(1, c.orders),
        group: c.action,
        extra: [
          { label: 'Neto', value: money(c.net) },
          { label: 'ROI', value: percent(c.roi) },
        ],
      })),
    [d],
  )
  const csv = (d?.creators ?? []).map((c) => ({
    creador: c.creator,
    decision: ACTION[c.action].label,
    muestras: c.samples,
    costo_muestras: c.sample_cost,
    ordenes: c.orders,
    ventas: c.gmv,
    utilidad: c.profit,
    neto: c.net,
  }))

  return (
    <section className="space-y-4">
      <h2 className="text-xl font-semibold">Decisiones sobre creadores</h2>
      <ErrorAlert message={data.error} />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {ORDER.map((a) => (
          <Kpi
            key={a}
            title={ACTION[a].label}
            value={count(d?.summary[a].creators ?? 0)}
            hint={`${money(d?.summary[a].sample_cost ?? 0)} en muestras · neto ${money(d?.summary[a].net ?? 0)}`}
            className={
              a === 'cut' && (d?.summary.cut.creators ?? 0) > 0 ? 'text-red-600' : a === 'repeat' ? 'text-green-700' : ''
            }
          />
        ))}
      </div>
      <p className="text-muted-foreground max-w-3xl text-sm">
        {pct1(d?.summary.conversion)} de los creadores con muestra ha vendido, y tarda una mediana de{' '}
        {d?.summary.median_days_to_first_sale ?? '—'} días en su primera venta. Dejar de enviar a quienes no vendieron
        evitaría <strong>{money(d?.summary.avoidable_cost ?? 0)}</strong> en muestras.
      </p>

      <ChartCard
        title="Costo de la muestra vs retorno por creador"
        subtitle="Sobre la diagonal el creador ya cubrió su muestra. Tamaño = órdenes, color = decisión. Clic en una burbuja para ver su fila."
        csv={csv}
        filename="creadores-costo-vs-retorno"
      >
        {data.loading && !d ? (
          <EmptyChart>Cargando…</EmptyChart>
        ) : (
          <BreakEvenBubble
            points={points}
            groups={Object.fromEntries(ORDER.map((a) => [a, { label: ACTION[a].label, color: ACTION_COLOR[a] }]))}
            xLabel="Costo de muestras"
            yLabel="Utilidad generada"
            zLabel="Órdenes"
            format={money}
            onPointClick={(p) => {
              setAction('all')
              setFocus(p.id)
            }}
          />
        )}
      </ChartCard>

      {focus && (
        <button type="button" onClick={() => setFocus(null)} className="rounded border px-2 py-1 text-xs hover:bg-slate-50">
          Mostrando solo @{focus} · quitar filtro
        </button>
      )}

      <Segmented
        label="Acción"
        value={action}
        options={[
          { value: 'all', label: 'Todos' },
          ...ORDER.map((a) => ({ value: a, label: ACTION[a].label, count: d?.summary[a].creators })),
        ]}
        onChange={setAction}
      />
      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Creador</TableHead>
                <TableHead>Acción</TableHead>
                <TableHead className="text-right">Muestras</TableHead>
                <TableHead className="text-right">Costo</TableHead>
                <TableHead className="text-right">Órdenes</TableHead>
                <TableHead className="text-right">Costo por orden</TableHead>
                <TableHead className="text-right">Días a 1ª venta</TableHead>
                <TableHead className="text-right">Neto</TableHead>
                <TableHead className="text-right">ROI</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} className="text-muted-foreground py-8 text-center">
                    {data.loading ? 'Cargando…' : 'Sin creadores con este filtro.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.slice(0, 60).map((c) => (
                <TableRow key={c.creator}>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-medium">@{c.creator}</span>
                      <span className="text-muted-foreground text-xs">desde {c.first_sample ?? '—'}</span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Pill label={ACTION[c.action].label} className={ACTION[c.action].pill} />
                  </TableCell>
                  <TableCell className="text-right">{count(c.samples)}</TableCell>
                  <TableCell className="text-right">{money(c.sample_cost)}</TableCell>
                  <TableCell className="text-right">
                    {count(c.orders)}
                    {c.orders_pending > 0 && (
                      <span className="text-muted-foreground text-xs"> (+{c.orders_pending})</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">{money(c.cost_per_order)}</TableCell>
                  <TableCell className="text-right">{c.days_to_first_sale ?? '—'}</TableCell>
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
          <CardTitle>Qué tipo de contenido vende más</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tipo</TableHead>
                <TableHead className="text-right">Órdenes</TableHead>
                <TableHead className="text-right">Ventas (base)</TableHead>
                <TableHead className="text-right">Comisión</TableHead>
                <TableHead className="text-right">Tasa de comisión</TableHead>
                <TableHead className="text-right">Creadores</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(d?.by_content ?? []).map((c) => (
                <TableRow key={c.content_type}>
                  <TableCell className="font-medium">{c.content_type}</TableCell>
                  <TableCell className="text-right">{count(c.orders)}</TableCell>
                  <TableCell className="text-right">{money(c.gmv)}</TableCell>
                  <TableCell className="text-right">{money(c.commission)}</TableCell>
                  <TableCell className="text-right">{pct1(c.commission_rate)}</TableCell>
                  <TableCell className="text-right">{count(c.creators)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <Hint>
        Reglas: <strong>Repetir</strong> = ya dejó utilidad y tiene al menos 2 órdenes; <strong>Esperar</strong> = sin
        ventas pero con muestra de menos de {d?.wait_days ?? 14} días; <strong>Dejar de enviar</strong> = sin ventas
        después de ese plazo; <strong>Observar</strong> = vende pero todavía no cubre el costo de la muestra. Entre
        paréntesis, órdenes aún sin liquidar (su utilidad se suma cuando TikTok las liquide). Son reglas simples para
        priorizar, no una predicción.
      </Hint>
    </section>
  )
}
