import { useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { Callout, ErrorAlert, Hint, NumberField, PILL, Pill, pct1 } from '@/components/analytics/ui'
import { Waterfall, type WaterfallStep } from '@/components/charts/Waterfall'
import { axisMoney, ChartCard, COLOR, EmptyChart, TipBox } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { CHANNEL_LABEL, type Channel } from '@/lib/api'
import { money } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import {
  aggregateBaseline,
  fetchSimulatorBaseline,
  leverGains,
  loadScenarios,
  monthlyDeltas,
  NO_CHANGES,
  profitCurve,
  sensitivity,
  simulate,
  storeScenarios,
  type Baseline,
  type Changes,
  type SavedScenario,
  type SimRow,
} from '@/lib/simulator'
import { useAsync } from '@/lib/useAsync'

const signed = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${money(Math.abs(v))}`
const deltaTone = (v: number) => (v > 0.005 ? 'text-emerald-600' : v < -0.005 ? 'text-red-600' : 'text-muted-foreground')
const num1 = (v: number) => (Math.round(v * 10) / 10).toString()

interface Option {
  key: string
  label: string
  rows: SimRow[]
}

function buildOptions(rows: SimRow[]): Option[] {
  const out: Option[] = [{ key: 'all', label: 'Todo lo filtrado', rows }]
  const channels = [...new Set(rows.map((r) => r.channel))]
  if (channels.length > 1)
    for (const ch of channels) out.push({ key: `ch:${ch}`, label: `Todo ${CHANNEL_LABEL[ch as Channel] ?? ch}`, rows: rows.filter((r) => r.channel === ch) })
  const bySku = new Map<string, SimRow[]>()
  for (const r of rows) bySku.set(r.sku, [...(bySku.get(r.sku) ?? []), r])
  for (const [sku, list] of bySku) {
    const name = list[0].product_name ? ` · ${list[0].product_name}` : ''
    if (list.length > 1) out.push({ key: `sku:${sku}`, label: `${sku}${name} · todos los canales`, rows: list })
    for (const r of list) out.push({ key: `row:${sku}|${r.channel}`, label: `${sku}${name} · ${CHANNEL_LABEL[r.channel] ?? r.channel}`, rows: [r] })
  }
  return out
}

function Control({
  label,
  value,
  onChange,
  min,
  max,
  step,
  suffix,
  hint,
}: {
  label: string
  value: number
  onChange: (v: number) => void
  min: number
  max: number
  step: number
  suffix: string
  hint?: string
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="text-sm font-medium">{label}</div>
          {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
        </div>
        <NumberField label="" value={value} onCommit={onChange} step={step} suffix={suffix} />
      </div>
      <input
        type="range"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={Math.min(max, Math.max(min, value))}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-blue-600"
      />
    </div>
  )
}

function Compare({ title, before, after, format, higherIsBetter = true }: { title: string; before: number | null; after: number | null; format: (v: number | null) => string; higherIsBetter?: boolean }) {
  const diff = before != null && after != null ? after - before : null
  const good = diff == null ? 0 : higherIsBetter ? diff : -diff
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1">
        <div className="text-muted-foreground flex items-baseline justify-between text-xs">
          <span>Antes</span>
          <span className="tabular-nums">{format(before)}</span>
        </div>
        <div className="flex items-baseline justify-between">
          <span className="text-muted-foreground text-xs">Después</span>
          <span className="text-2xl font-semibold tabular-nums">{format(after)}</span>
        </div>
        <div className={`text-right text-xs font-medium tabular-nums ${deltaTone(good)}`}>
          {diff == null ? '—' : diff === 0 ? 'sin cambio' : `${diff > 0 ? '+' : '−'}${format(Math.abs(diff))}`}
        </div>
      </CardContent>
    </Card>
  )
}

export function Simulator() {
  const { preset, channelParam, account } = useScope()
  const days = daysFromPreset(preset)
  const scope = useScopeDescription()
  const data = useAsync(() => fetchSimulatorBaseline({ days, channel: channelParam, account }), [days, channelParam, account])
  const d = data.data

  const [choice, setChoice] = useState('all')
  const [search, setSearch] = useState('')
  const [changes, setChanges] = useState<Changes>(NO_CHANGES)
  const [saved, setSaved] = useState<SavedScenario[]>(loadScenarios)
  const [name, setName] = useState('')
  const [storageFailed, setStorageFailed] = useState(false)

  const options = useMemo(() => buildOptions(d?.rows ?? []), [d?.rows])
  const selected = options.find((o) => o.key === choice) ?? options[0]

  const needle = search.trim().toLowerCase()
  const visibleOptions = options.filter((o) => o.key === selected?.key || !needle || o.label.toLowerCase().includes(needle))

  const base: Baseline | null = useMemo(() => (selected ? aggregateBaseline(selected.rows, days) : null), [selected, days])
  const set = (patch: Partial<Changes>) => setChanges((c) => ({ ...c, ...patch }))

  const before = useMemo(() => (base ? simulate(base, { ...NO_CHANGES, includeAds: changes.includeAds }) : null), [base, changes.includeAds])
  const after = useMemo(() => (base ? simulate(base, changes) : null), [base, changes])
  const curve = useMemo(() => (base ? profitCurve(base, changes) : null), [base, changes])
  const tornado = useMemo(() => (base ? sensitivity(base, changes) : []), [base, changes])
  const deltas = useMemo(() => (base ? monthlyDeltas(base, changes) : []), [base, changes])

  const steps = useMemo<WaterfallStep[]>(() => {
    if (!before || !after) return []
    const moves = deltas.filter((s) => Math.abs(s.value) >= 0.005).map((s) => ({ id: s.id, label: s.label, kind: 'delta' as const, value: s.value }))
    return [{ id: 'start', label: 'Utilidad actual', kind: 'total' as const, value: before.monthlyProfit }, ...moves, { id: 'end', label: 'Utilidad nueva', kind: 'total' as const, value: after.monthlyProfit }]
  }, [before, after, deltas])

  const levers = useMemo(() => {
    const rows = (d?.rows ?? [])
      .map((r) => {
        const b = aggregateBaseline([r], days)
        if (!b) return null
        const list = leverGains(b, changes.elasticity, changes.includeAds)
        const best = list.reduce((a, x) => (x.gain > a.gain ? x : a), list[0])
        return { row: r, list, best }
      })
      .filter((x): x is NonNullable<typeof x> => x != null)
    return rows.sort((a, b) => b.best.gain - a.best.gain).slice(0, 20)
  }, [d?.rows, days, changes.elasticity, changes.includeAds])

  const changed = JSON.stringify({ ...changes, includeAds: false }) !== JSON.stringify({ ...NO_CHANGES })
  const optimumLabel = curve && (curve.edge ? null : `${money(curve.best.price)} (${curve.best.pricePct > 0 ? '+' : ''}${curve.best.pricePct}%)`)

  const save = () => {
    const n = name.trim()
    if (!n) return
    const next = [{ id: `${Date.now()}`, name: n, target: selected?.label ?? '', changes, savedAt: new Date().toISOString() }, ...saved].slice(0, 20)
    setSaved(next)
    setStorageFailed(!storeScenarios(next))
    setName('')
  }
  const remove = (id: string) => {
    const next = saved.filter((s) => s.id !== id)
    setSaved(next)
    setStorageFailed(!storeScenarios(next))
  }

  const empty = !data.loading && !data.error && (d?.rows.length ?? 0) === 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Simulador ¿y si…?{scope ? ` · ${scope}` : ''}</h1>
        {d && (
          <p className="text-muted-foreground text-xs">
            Base: lo que pasó del {d.start} al {d.end} ({d.days} días, según el período global)
          </p>
        )}
      </div>

      <Callout tone="info">
        Es una proyección con la economía de los últimos {d?.days ?? days} días, no una garantía: supone que el volumen, la mezcla de ventas y los costos se mantienen como en ese período.
      </Callout>

      <ErrorAlert message={data.error} />
      {data.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}
      {empty && <Callout tone="warning">No hay ventas en este período y filtro. Cambia el período o la tienda arriba.</Callout>}

      {d && d.skus_without_cost > 0 && (
        <Callout tone="warning">
          {d.skus_without_cost} SKU no tienen costo cargado: su utilidad base no resta el producto y se ve más alta de lo real.
        </Callout>
      )}

      {d && d.rows.length > 0 && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">¿Qué quieres simular?</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Buscar SKU o nombre"
                  aria-label="Buscar SKU o nombre"
                  className="h-9 w-full rounded-md border px-3 text-sm sm:w-64"
                />
                <select
                  value={selected?.key}
                  onChange={(e) => setChoice(e.target.value)}
                  aria-label="SKU o canal a simular"
                  className="h-9 w-full rounded-md border bg-transparent px-2 text-sm sm:flex-1"
                >
                  {visibleOptions.map((o) => (
                    <option key={o.key} value={o.key}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              {base ? (
                <div className="text-muted-foreground grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4 lg:grid-cols-8">
                  <span>Unidades: <b className="text-foreground">{base.units}</b></span>
                  <span>Por mes: <b className="text-foreground">{num1(base.unitsPerMonth)}</b></span>
                  <span>Precio: <b className="text-foreground">{money(base.price)}</b></span>
                  <span>Promo: <b className="text-foreground">{money(base.promo)}</b></span>
                  <span>Comisión: <b className="text-foreground">{pct1(base.feeRate)}</b></span>
                  <span>Envío: <b className="text-foreground">{money(base.shipping)}</b></span>
                  <span>Reembolsos: <b className="text-foreground">{pct1(base.refundRate)}</b></span>
                  <span>Costo: <b className="text-foreground">{base.hasCost ? money(base.cogs) : 'sin costo'}</b></span>
                </div>
              ) : (
                <Callout tone="warning">Esta selección no tiene ventas en el período: no hay base para simular.</Callout>
              )}
            </CardContent>
          </Card>

          {base && before && after && curve && (
            <>
              <div className="grid gap-4 xl:grid-cols-[minmax(0,22rem)_1fr]">
                <Card>
                  <CardHeader className="flex flex-row items-center justify-between">
                    <CardTitle className="text-base">Controles</CardTitle>
                    <button
                      type="button"
                      onClick={() => setChanges(NO_CHANGES)}
                      disabled={!changed && changes.elasticity === 0}
                      className="rounded border px-2 py-1 text-xs hover:bg-slate-100 disabled:opacity-40"
                    >
                      Restablecer
                    </button>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <Control label="Precio" value={changes.pricePct} onChange={(v) => set({ pricePct: v })} min={-50} max={100} step={1} suffix="%" hint={`${money(base.price)} → ${money(after.price)}`} />
                    <Control label="Costo (COGS)" value={changes.costPct} onChange={(v) => set({ costPct: v })} min={-50} max={100} step={1} suffix="%" hint={`${money(base.cogs)} → ${money(after.cogs)}`} />
                    <Control label="Promoción por unidad" value={changes.promoPct} onChange={(v) => set({ promoPct: v })} min={-100} max={200} step={5} suffix="%" hint={`${money(base.promo)} → ${money(after.promo)}`} />
                    <Control label="Comisión de la plataforma" value={changes.commissionPts} onChange={(v) => set({ commissionPts: v })} min={-10} max={10} step={0.5} suffix="pts" hint={`${pct1(base.feeRate)} → ${pct1(Math.max(0, base.feeRate + changes.commissionPts / 100))} de la venta neta`} />
                    <Control label="Costo de envío" value={changes.shippingPct} onChange={(v) => set({ shippingPct: v })} min={-50} max={100} step={1} suffix="%" hint={`${money(base.shipping)} → ${money(after.shipping)}`} />
                    <Control label="Unidades" value={changes.unitsPct} onChange={(v) => set({ unitsPct: v })} min={-90} max={300} step={5} suffix="%" hint={`${num1(base.unitsPerMonth)} → ${num1(after.unitsPerMonth)} al mes`} />
                    <Control label="Elasticidad del precio" value={changes.elasticity} onChange={(v) => set({ elasticity: v })} min={0} max={5} step={0.1} suffix="x" hint="Si el precio sube 1%, las unidades bajan esto en %" />
                    <label className="flex items-center gap-2 text-xs">
                      <input type="checkbox" checked={changes.includeAds} onChange={(e) => set({ includeAds: e.target.checked })} />
                      Restar la publicidad asignada ({money(base.adMonthly)} al mes, no cambia con el escenario)
                    </label>
                  </CardContent>
                </Card>

                <div className="space-y-4">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Compare title="Utilidad por unidad" before={before.profitPerUnit} after={after.profitPerUnit} format={(v) => money(v)} />
                    <Compare title="Utilidad mensual" before={before.monthlyProfit} after={after.monthlyProfit} format={(v) => money(v)} />
                    <Compare title="Margen" before={before.margin} after={after.margin} format={pct1} />
                    <Compare title="ROI sobre el costo" before={before.roi} after={after.roi} format={(v) => (v == null ? '—' : `${(v * 100).toFixed(0)}%`)} />
                  </div>
                  <Card>
                    <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-1 pt-6 text-sm">
                      <span>
                        Punto de equilibrio del precio:{' '}
                        <b>{after.breakEvenPrice == null ? 'sin equilibrio posible' : money(after.breakEvenPrice)}</b>
                      </span>
                      {after.breakEvenPrice != null && (
                        <Pill
                          label={after.price >= after.breakEvenPrice ? 'Precio sobre el equilibrio' : 'Precio bajo el equilibrio'}
                          className={after.price >= after.breakEvenPrice ? PILL.green : PILL.red}
                        />
                      )}
                    </CardContent>
                  </Card>
                </div>
              </div>

              <div className="grid gap-4 xl:grid-cols-2">
                <ChartCard
                  title="Cambio de la utilidad mensual por concepto"
                  subtitle="Se aplican los cambios uno a uno; cada barra es lo que aporta ese concepto."
                  filename="simulador-cascada"
                  csv={steps.map((s) => ({ Concepto: s.label, Monto: Math.round(s.value * 100) / 100 }))}
                >
                  {steps.length <= 2 && !changed ? <EmptyChart>Mueve un control para ver el efecto.</EmptyChart> : <Waterfall steps={steps} height={300} />}
                </ChartCard>

                <ChartCard
                  title="Utilidad mensual contra precio"
                  subtitle={
                    curve.edge
                      ? 'Con esta elasticidad no hay un precio óptimo: la utilidad sigue mejorando hacia un extremo del rango. Sube la elasticidad para ver el óptimo.'
                      : `Con la elasticidad dada, el mejor precio es ${optimumLabel}.`
                  }
                  filename="simulador-curva-precio"
                  csv={curve.points.map((p) => ({ 'Precio': Math.round(p.price * 100) / 100, 'Utilidad mensual': Math.round(p.monthlyProfit * 100) / 100 }))}
                >
                  <ResponsiveContainer width="100%" height={300}>
                    <LineChart data={curve.points} margin={{ top: 16, right: 12, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis type="number" dataKey="price" domain={['dataMin', 'dataMax']} tickFormatter={(v: number) => `$${v.toFixed(0)}`} tick={{ fontSize: 11 }} />
                      <YAxis tickFormatter={axisMoney} tick={{ fontSize: 11 }} width={56} />
                      <Tooltip
                        content={({ active, payload }) => {
                          const p = active ? (payload?.[0]?.payload as { price: number; pricePct: number; monthlyProfit: number } | undefined) : undefined
                          return p ? (
                            <TipBox
                              title={`Precio ${money(p.price)} (${p.pricePct > 0 ? '+' : ''}${p.pricePct}%)`}
                              rows={[{ label: 'Utilidad mensual', value: money(p.monthlyProfit) }]}
                            />
                          ) : null
                        }}
                      />
                      <ReferenceLine y={0} stroke={COLOR.neutral} />
                      <ReferenceLine x={base.price} stroke={COLOR.neutral} strokeDasharray="4 3" label={{ value: 'Actual', position: 'insideTopLeft', fontSize: 11 }} />
                      <ReferenceLine x={after.price} stroke={COLOR.blue} label={{ value: 'Propuesto', position: 'insideTopRight', fontSize: 11 }} />
                      {!curve.edge && <ReferenceLine x={curve.best.price} stroke={COLOR.good} strokeDasharray="2 2" label={{ value: 'Óptimo', position: 'top', fontSize: 11 }} />}
                      <Line type="monotone" dataKey="monthlyProfit" stroke={COLOR.blue} strokeWidth={2} dot={false} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </ChartCard>
              </div>

              <ChartCard
                title="Sensibilidad: ¿qué mueve más la utilidad?"
                subtitle="Cuánto cambia la utilidad mensual si cada variable sube o baja 10% desde el escenario actual (comisión: 10% de la comisión)."
                filename="simulador-tornado"
                csv={tornado.map((t) => ({ Variable: t.label, 'Baja 10%': Math.round(t.down * 100) / 100, 'Sube 10%': Math.round(t.up * 100) / 100 }))}
              >
                <ResponsiveContainer width="100%" height={Math.max(220, tornado.length * 48)}>
                  <BarChart data={tornado} layout="vertical" stackOffset="sign" margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" tickFormatter={axisMoney} tick={{ fontSize: 11 }} />
                    <YAxis type="category" dataKey="label" width={92} tick={{ fontSize: 11 }} />
                    <Tooltip
                      content={({ active, payload }) => {
                        const t = active ? (payload?.[0]?.payload as (typeof tornado)[number] | undefined) : undefined
                        return t ? (
                          <TipBox
                            title={t.label}
                            rows={[
                              { label: 'Baja 10%', value: signed(t.down) },
                              { label: 'Sube 10%', value: signed(t.up) },
                            ]}
                          />
                        ) : null
                      }}
                    />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <ReferenceLine x={0} stroke={COLOR.neutral} />
                    <Bar dataKey="down" name="Baja 10%" fill={COLOR.amber} stackId="t" isAnimationActive={false} />
                    <Bar dataKey="up" name="Sube 10%" fill={COLOR.blue} stackId="t" isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>
            </>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Escenarios guardados</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && save()}
                  placeholder="Nombre del escenario (p. ej. Subir 5% y bajar envío)"
                  aria-label="Nombre del escenario"
                  maxLength={60}
                  className="h-9 w-full rounded-md border px-3 text-sm sm:w-96"
                />
                <button type="button" onClick={save} disabled={!name.trim() || !base} className="rounded border px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-40">
                  Guardar escenario
                </button>
              </div>
              {storageFailed && <Callout tone="warning">No se pudo guardar en este navegador; los escenarios se pierden al cerrar la pantalla.</Callout>}
              {saved.length === 0 ? (
                <p className="text-muted-foreground text-sm">Aún no hay escenarios. Guarda uno para compararlo luego con otros sobre lo seleccionado.</p>
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Escenario</TableHead>
                        <TableHead className="text-right">Utilidad/unidad</TableHead>
                        <TableHead className="text-right">Utilidad mensual</TableHead>
                        <TableHead className="text-right">vs. actual</TableHead>
                        <TableHead />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {saved.map((s) => {
                        const o = base ? simulate(base, s.changes) : null
                        const diff = o && before ? o.monthlyProfit - before.monthlyProfit : null
                        return (
                          <TableRow key={s.id}>
                            <TableCell>
                              <div className="font-medium">{s.name}</div>
                              <div className="text-muted-foreground max-w-xs truncate text-xs">guardado sobre: {s.target}</div>
                            </TableCell>
                            <TableCell className="text-right tabular-nums">{o ? money(o.profitPerUnit) : '—'}</TableCell>
                            <TableCell className="text-right tabular-nums">{o ? money(o.monthlyProfit) : '—'}</TableCell>
                            <TableCell className={`text-right tabular-nums ${diff == null ? '' : deltaTone(diff)}`}>{diff == null ? '—' : signed(diff)}</TableCell>
                            <TableCell className="space-x-2 text-right whitespace-nowrap">
                              <button type="button" onClick={() => setChanges(s.changes)} className="text-xs underline">
                                Cargar
                              </button>
                              <button type="button" onClick={() => remove(s.id)} className="text-xs text-red-600 underline">
                                Borrar
                              </button>
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}
              <Hint>La comparación aplica cada escenario a lo que está seleccionado ahora, con la base del período actual.</Hint>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Mejores palancas por SKU</CardTitle>
              <p className="text-muted-foreground text-xs">
                Ganancia mensual con un ajuste moderado de cada palanca (el precio usa la elasticidad de los controles). Clic en una fila para simularla.
              </p>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>SKU</TableHead>
                    <TableHead className="text-right">Utilidad mensual</TableHead>
                    {levers[0]?.list.map((l) => (
                      <TableHead key={l.key} className="text-right">
                        {l.label}
                      </TableHead>
                    ))}
                    <TableHead>Mejor palanca</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {levers.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={9} className="text-muted-foreground py-8 text-center">
                        Sin SKUs con ventas en este período.
                      </TableCell>
                    </TableRow>
                  )}
                  {levers.map(({ row, list, best }) => (
                    <TableRow
                      key={`${row.sku}|${row.channel}`}
                      className="cursor-pointer hover:bg-slate-50"
                      onClick={() => setChoice(`row:${row.sku}|${row.channel}`)}
                    >
                      <TableCell>
                        <div className="font-mono text-xs">{row.sku}</div>
                        <div className="text-muted-foreground max-w-xs truncate text-xs">
                          {CHANNEL_LABEL[row.channel] ?? row.channel}
                          {row.product_name ? ` · ${row.product_name}` : ''}
                        </div>
                      </TableCell>
                      <TableCell className={`text-right tabular-nums ${row.monthly_profit < 0 ? 'text-red-600' : ''}`}>{money(row.monthly_profit)}</TableCell>
                      {list.map((l) => (
                        <TableCell key={l.key} className={`text-right tabular-nums ${l.key === best.key ? 'font-semibold text-emerald-700' : deltaTone(l.gain)}`}>
                          {signed(l.gain)}
                        </TableCell>
                      ))}
                      <TableCell>{best.gain > 0.005 ? <Pill label={best.label} className={PILL.green} /> : '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
