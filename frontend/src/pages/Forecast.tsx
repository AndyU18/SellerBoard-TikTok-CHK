import { useMemo, useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { ChannelBadge } from '@/components/ChannelBadge'
import { ErrorAlert, Hint, Kpi, NumberField, PILL, Pill, Segmented, pct1 } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { CHANNEL_LABEL, CHANNELS, type Channel } from '@/lib/api'
import { analytics, type GoalMetric, type GoalProgress } from '@/lib/analytics'
import { count, money, tone } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const METRIC: Record<GoalMetric, { label: string; format: (v: number) => string }> = {
  gross_sales: { label: 'Ventas', format: (v) => money(v) },
  net_profit: { label: 'Utilidad', format: (v) => money(v) },
  units: { label: 'Unidades', format: (v) => count(Math.round(v)) },
}
const METRIC_OPTIONS = (Object.keys(METRIC) as GoalMetric[]).map((k) => ({ value: k, label: METRIC[k].label }))

const STATUS = {
  on_track: { label: 'Va en camino', pill: PILL.green, bar: 'bg-green-500' },
  at_risk: { label: 'En riesgo', pill: PILL.amber, bar: 'bg-amber-500' },
  behind: { label: 'Atrasada', pill: PILL.red, bar: 'bg-red-500' },
  no_target: { label: 'Sin meta', pill: PILL.slate, bar: 'bg-slate-400' },
} as const

const thisMonth = () => new Date().toISOString().slice(0, 7)

function GoalCard({ goal, onDelete }: { goal: GoalProgress; onDelete: (id: number) => void }) {
  const m = METRIC[goal.metric]
  const st = STATUS[goal.status]
  const done = Math.min(100, Math.max(0, (goal.attainment ?? 0) * 100))
  const proj = Math.min(100, Math.max(0, (goal.projected_attainment ?? 0) * 100))
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-base">
          <span className="flex items-center gap-2">
            {m.label}{' '}
            {goal.channel ? (
              <ChannelBadge channel={goal.channel} />
            ) : (
              <span className="text-xs font-normal">todos los canales</span>
            )}
          </span>
          <Pill label={st.label} className={st.pill} />
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div
          className="relative h-3 overflow-hidden rounded bg-slate-200"
          role="img"
          aria-label={`Avance ${done.toFixed(0)} %, proyectado ${proj.toFixed(0)} %`}
        >
          <div className="absolute inset-y-0 left-0 bg-slate-400/60" style={{ width: `${proj}%` }} />
          <div className={`absolute inset-y-0 left-0 ${st.bar}`} style={{ width: `${done}%` }} />
        </div>
        <div className="flex flex-wrap justify-between gap-2 text-sm">
          <span>
            <strong>{m.format(goal.actual)}</strong> de {m.format(goal.target)} ({pct1(goal.attainment)})
          </span>
          <span className="text-muted-foreground">
            proyectado al cierre: {m.format(goal.projected)} ({pct1(goal.projected_attainment)})
          </span>
        </div>
        <div className="text-muted-foreground flex flex-wrap items-center justify-between gap-2 text-xs">
          <span>
            {goal.days_left > 0 && goal.required_per_day != null
              ? `Faltan ${goal.days_left} días: necesitas ${m.format(Math.max(0, goal.required_per_day))} por día.`
              : 'El mes ya cerró.'}
          </span>
          <Button variant="ghost" size="sm" onClick={() => onDelete(goal.id)}>
            Quitar
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

export function Forecast() {
  const scope = useScope()
  const [horizon, setHorizon] = useState(30)
  const [multiplier, setMultiplier] = useState(1)
  const [metric, setMetric] = useState<GoalMetric>('gross_sales')
  const [month, setMonth] = useState(thisMonth())
  const [version, setVersion] = useState(0)
  const [goalError, setGoalError] = useState<string | null>(null)
  const [newMetric, setNewMetric] = useState<GoalMetric>('gross_sales')
  const [newChannel, setNewChannel] = useState<Channel | ''>('')
  const [target, setTarget] = useState('')

  const sc = useMemo(
    () => ({ channel: scope.channelParam, account: scope.account }),
    [scope.channelParam, scope.account],
  )
  const forecast = useAsync(() => analytics.forecast(sc, { horizon, multiplier }), [sc, horizon, multiplier])
  const goals = useAsync(() => analytics.goals(`${month}-01`, multiplier), [month, multiplier, version])

  const chart = useMemo(
    () =>
      (forecast.data?.series ?? []).map((p) => ({
        date: p.date.slice(5),
        Real: p.forecast ? null : p[metric],
        Pronóstico: p.forecast ? p[metric] : null,
      })),
    [forecast.data, metric],
  )
  const t = forecast.data?.totals

  async function addGoal() {
    setGoalError(null)
    const value = Number(target)
    if (!target || Number.isNaN(value) || value < 0) {
      setGoalError('Escribe una meta numérica')
      return
    }
    try {
      await analytics.saveGoal({ month: `${month}-01`, metric: newMetric, target: value, channel: newChannel || null })
      setTarget('')
      setVersion((v) => v + 1)
    } catch (e) {
      setGoalError(e instanceof Error ? e.message : 'No se pudo guardar la meta')
    }
  }

  async function removeGoal(id: number) {
    setGoalError(null)
    try {
      await analytics.deleteGoal(id)
      setVersion((v) => v + 1)
    } catch (e) {
      setGoalError(e instanceof Error ? e.message : 'No se pudo quitar la meta')
    }
  }

  const changeText = (v: number | null | undefined) =>
    v == null ? '' : ` · ${v >= 0 ? '+' : ''}${(v * 100).toFixed(0)} % vs. ritmo actual`

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold">Pronóstico y metas</h1>
        <div className="flex flex-wrap items-end gap-4">
          <Segmented
            label="Horizonte"
            value={String(horizon)}
            options={[
              { value: '30', label: '30 días' },
              { value: '60', label: '60 días' },
            ]}
            onChange={(v) => setHorizon(Number(v))}
          />
          <NumberField
            label="Temporada"
            value={multiplier}
            min={0.1}
            max={10}
            step={0.1}
            suffix="× el ritmo actual"
            onCommit={setMultiplier}
          />
        </div>
      </div>

      <ErrorAlert message={forecast.error} />

      <div className="grid gap-4 md:grid-cols-3">
        {METRIC_OPTIONS.map(({ value: k }) => (
          <Kpi
            key={k}
            title={`${METRIC[k].label} esperadas (${horizon} d)`}
            value={t ? METRIC[k].format(t[k].forecast) : '—'}
            hint={
              t ? `rango ${METRIC[k].format(t[k].low)} – ${METRIC[k].format(t[k].high)}${changeText(t[k].change)}` : undefined
            }
            className={k === 'net_profit' ? tone(t?.[k].forecast) : ''}
          />
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center justify-between gap-3">
            <span>Real y pronóstico por día</span>
            <Segmented label="Métrica" value={metric} options={METRIC_OPTIONS} onChange={setMetric} />
          </CardTitle>
        </CardHeader>
        <CardContent className="h-72">
          {chart.length === 0 ? (
            <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
              {forecast.loading ? 'Cargando…' : 'Sin datos.'}
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chart}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" minTickGap={24} />
                <YAxis
                  tickFormatter={(v: number) => (metric === 'units' ? `${v}` : `$${v.toLocaleString('en-US')}`)}
                  width={72}
                />
                <Tooltip formatter={(v) => METRIC[metric].format(Number(v))} />
                <Legend />
                <Line type="monotone" dataKey="Real" stroke="#2563eb" dot={false} />
                <Line type="monotone" dataKey="Pronóstico" stroke="#f59e0b" strokeDasharray="6 4" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <section className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 className="text-xl font-semibold">Metas del mes</h2>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Mes</span>
            <Input
              type="month"
              className="h-9 w-44"
              value={month}
              onChange={(e) => e.target.value && setMonth(e.target.value)}
            />
          </label>
        </div>
        <ErrorAlert message={goals.error} />
        {goalError && (
          <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
            {goalError}
          </div>
        )}

        <div className="grid gap-4 xl:grid-cols-2">
          {(goals.data?.goals ?? []).map((g) => (
            <GoalCard key={g.id} goal={g} onDelete={removeGoal} />
          ))}
          {(goals.data?.goals.length ?? 0) === 0 && (
            <p className="text-muted-foreground text-sm">
              {goals.loading ? 'Cargando…' : 'No hay metas para este mes. Agrega una abajo.'}
            </p>
          )}
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Agregar o cambiar una meta</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-4">
            <Segmented label="Métrica de la meta" value={newMetric} options={METRIC_OPTIONS} onChange={setNewMetric} />
            <Segmented
              label="Canal de la meta"
              value={newChannel || 'all'}
              options={[{ value: 'all', label: 'Todos' }, ...CHANNELS.map((c) => ({ value: c, label: CHANNEL_LABEL[c] }))]}
              onChange={(v) => setNewChannel(v === 'all' ? '' : (v as Channel))}
            />
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-muted-foreground text-xs">Meta</span>
              <Input
                type="number"
                min={0}
                className="h-9 w-36"
                value={target}
                placeholder="10000"
                onChange={(e) => setTarget(e.target.value)}
              />
            </label>
            <Button size="sm" onClick={addGoal}>
              Guardar meta
            </Button>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Productos que más aportarán</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Producto</TableHead>
                <TableHead className="text-right">Venta diaria</TableHead>
                <TableHead className="text-right">Unidades esperadas</TableHead>
                <TableHead className="text-right">Ventas esperadas</TableHead>
                <TableHead className="text-right">Utilidad esperada</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(forecast.data?.by_sku.length ?? 0) === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground py-8 text-center">
                    {forecast.loading ? 'Cargando…' : 'Sin ventas recientes.'}
                  </TableCell>
                </TableRow>
              )}
              {(forecast.data?.by_sku ?? []).map((r) => (
                <TableRow key={`${r.channel}-${r.sku}`}>
                  <TableCell>
                    <div className="flex min-w-48 flex-col">
                      <span className="font-medium">{r.product_name ?? r.sku}</span>
                      <span className="text-muted-foreground flex items-center gap-2 text-xs">
                        {r.sku} <ChannelBadge channel={r.channel} />
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">{r.daily_units.toFixed(1)}</TableCell>
                  <TableCell className="text-right">{count(Math.round(r.forecast_units))}</TableCell>
                  <TableCell className="text-right">{money(r.forecast_sales)}</TableCell>
                  <TableCell className={`text-right ${tone(r.forecast_profit)}`}>{money(r.forecast_profit)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Hint>
        El pronóstico proyecta el ritmo reciente (promedio que pesa más los últimos días
        {forecast.data?.weekday_adjusted ? ', ajustado por día de la semana' : ''}) por el factor de temporada que
        elijas; el rango son ±1.28 desviaciones estándar (aprox. percentiles 10 y 90). No conoce promociones futuras ni
        agotamientos de inventario. En las metas, “proyectado al cierre” suma lo logrado en el mes más el ritmo de las
        últimas 4 semanas para los días que faltan.
      </Hint>
    </div>
  )
}
