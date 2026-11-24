import { useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { ErrorAlert, Hint, PILL, Pill } from '@/components/analytics/ui'
import { Donut } from '@/components/charts/Donut'
import { ChartCard, COLOR, EmptyChart, TipBox } from '@/components/charts/kit'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { count, money } from '@/lib/format'
import { skus, type QualityCheck, type QualitySeverity } from '@/lib/skus'
import { useAsync } from '@/lib/useAsync'

const SEVERITY: Record<QualitySeverity, { label: string; pill: string; border: string }> = {
  critical: { label: 'Crítico', pill: PILL.red, border: 'border-l-red-500' },
  warning: { label: 'Advertencia', pill: PILL.amber, border: 'border-l-amber-500' },
  info: { label: 'Informativo', pill: PILL.blue, border: 'border-l-blue-500' },
  ok: { label: 'En orden', pill: PILL.green, border: 'border-l-green-500' },
}

const scoreColor = (score: number) =>
  score >= 90 ? 'text-green-600' : score >= 70 ? 'text-amber-600' : 'text-red-600'

const PENALTY: Record<QualitySeverity, number> = { critical: 20, warning: 8, info: 2, ok: 0 }

/** Área a la que pertenece cada revisión (para el puntaje por área). */
const AREA_OF: Record<string, string> = {
  missing_cost: 'Costos y catálogo',
  duplicate_skus: 'Costos y catálogo',
  nameless_products: 'Costos y catálogo',
  unassigned_lines: 'Costos y catálogo',
  unassigned_labels: 'Conciliación',
  zero_fee_lines: 'Conciliación',
  settlements: 'Conciliación',
  return_unknown_skus: 'Devoluciones',
  unknown_stock_skus: 'Inventario',
  orders_without_state: 'Órdenes',
  syncs: 'Sincronización',
}

const lightColor = (score: number) => (score >= 90 ? COLOR.good : score >= 70 ? COLOR.amber : COLOR.bad)
const lightLabel = (score: number) => (score >= 90 ? 'Verde' : score >= 70 ? 'Amarillo' : 'Rojo')

function QualityCharts({ checks, onNavigate }: { checks: QualityCheck[]; onNavigate?: (page: string) => void }) {
  const areas = useMemo(() => {
    const m = new Map<string, number>()
    for (const c of checks) m.set(AREA_OF[c.id] ?? 'Otros', (m.get(AREA_OF[c.id] ?? 'Otros') ?? 100) - PENALTY[c.severity])
    return [...m.entries()].map(([area, score]) => ({ area, score: Math.max(0, score) })).sort((a, b) => a.score - b.score)
  }, [checks])

  const bySeverity = (['critical', 'warning', 'info'] as const).map((k) => ({
    id: k,
    label: SEVERITY[k].label,
    value: checks.filter((c) => c.severity === k).length,
  }))

  const skuRows = useMemo(() => {
    const m = new Map<string, { sku: string; issues: number; titles: string[]; page: string }>()
    for (const c of checks) {
      if (c.severity === 'ok') continue
      for (const sku of c.skus ?? []) {
        const r = m.get(sku) ?? { sku, issues: 0, titles: [], page: c.action.page }
        r.issues += 1
        r.titles.push(c.title)
        m.set(sku, r)
      }
    }
    return [...m.values()].sort((a, b) => b.issues - a.issues || a.sku.localeCompare(b.sku)).slice(0, 12)
  }, [checks])

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <ChartCard
        title="Puntaje por área"
        subtitle="100 menos las penalizaciones de cada área. Verde 90+, amarillo 70+, rojo menos."
        filename="calidad-puntaje-por-area"
        csv={areas.map((a) => ({ area: a.area, puntaje: a.score, semaforo: lightLabel(a.score) }))}
      >
        <ResponsiveContainer width="100%" height={Math.max(180, areas.length * 38)}>
          <BarChart data={areas} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 11 }} />
            <YAxis type="category" dataKey="area" width={120} tick={{ fontSize: 11 }} />
            <Tooltip
              cursor={{ fill: 'rgba(148,163,184,0.15)' }}
              content={({ active, payload }) => {
                const p = active ? (payload?.[0]?.payload as { area: string; score: number } | undefined) : undefined
                return p ? <TipBox title={p.area} rows={[{ label: 'Puntaje', value: `${p.score} / 100`, color: lightColor(p.score) }, { label: 'Semáforo', value: lightLabel(p.score) }]} /> : null
              }}
            />
            <Bar dataKey="score" radius={[0, 3, 3, 0]}>
              {areas.map((a) => (
                <Cell key={a.area} fill={lightColor(a.score)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      <ChartCard
        title="Problemas por gravedad"
        subtitle="Número de revisiones con problemas, según su gravedad."
        filename="calidad-problemas-por-gravedad"
        csv={bySeverity.map((s) => ({ gravedad: s.label, revisiones: s.value }))}
      >
        <Donut slices={bySeverity} format={(v) => String(v)} centerLabel="revisiones con problemas" />
      </ChartCard>

      <ChartCard
        title="SKU con más problemas"
        subtitle="Clic en una barra para ir a la pantalla donde se corrige."
        filename="calidad-sku-con-problemas"
        className="lg:col-span-2"
        csv={skuRows.map((r) => ({ sku: r.sku, problemas: r.issues, revisiones: r.titles.join(' | ') }))}
      >
        {skuRows.length === 0 ? (
          <EmptyChart>Ningún SKU con problemas detectados.</EmptyChart>
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(160, skuRows.length * 32)}>
            <BarChart data={skuRows} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} />
              <YAxis type="category" dataKey="sku" width={110} tick={{ fontSize: 11 }} />
              <Tooltip
                cursor={{ fill: 'rgba(148,163,184,0.15)' }}
                content={({ active, payload }) => {
                  const p = active ? (payload?.[0]?.payload as (typeof skuRows)[number] | undefined) : undefined
                  return p ? <TipBox title={p.sku} rows={p.titles.map((t) => ({ label: t, value: '1' }))} /> : null
                }}
              />
              <Bar
                dataKey="issues"
                fill={COLOR.bad}
                radius={[0, 3, 3, 0]}
                cursor={onNavigate ? 'pointer' : undefined}
                onClick={(row: unknown) => {
                  const page = (row as { payload?: { page?: string } }).payload?.page
                  if (page) onNavigate?.(page)
                }}
              />
            </BarChart>
          </ResponsiveContainer>
        )}
      </ChartCard>
    </div>
  )
}

function CheckRow({ check, onNavigate }: { check: QualityCheck; onNavigate?: (page: string) => void }) {
  const s = SEVERITY[check.severity]
  return (
    <Card className={`border-l-4 ${s.border}`}>
      <CardContent className="flex flex-wrap items-start justify-between gap-3 pt-4">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{check.title}</span>
            <Pill label={s.label} className={s.pill} />
            {check.severity !== 'ok' && (
              <span className="text-muted-foreground text-xs">{count(check.count)} afectados</span>
            )}
            {check.impact != null && check.impact > 0 && (
              <span className="text-xs font-medium">{money(check.impact)} involucrados</span>
            )}
          </div>
          <p className="text-muted-foreground text-sm">{check.detail}</p>
        </div>
        {check.severity !== 'ok' && onNavigate && (
          <Button size="sm" variant="outline" onClick={() => onNavigate(check.action.page)}>
            {check.action.label}
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

export function DataQuality({ onNavigate }: { onNavigate?: (page: string) => void }) {
  const data = useAsync(() => skus.quality(), [])
  const d = data.data
  const open = (d?.checks ?? []).filter((c) => c.severity !== 'ok')
  const healthy = (d?.checks ?? []).filter((c) => c.severity === 'ok')

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Calidad de datos</h1>
      <Hint>
        Revisiones automáticas de datos faltantes o mal unidos que distorsionan la utilidad. La puntuación resta 20
        puntos por cada revisión crítica, 8 por advertencia y 2 por informativa.
      </Hint>
      <ErrorAlert message={data.error} />
      {data.loading && !d && <p className="text-muted-foreground text-sm">Calculando…</p>}

      {d && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Puntuación de calidad</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-6">
              <div className={`text-6xl font-bold ${scoreColor(d.score)}`} aria-label={`Puntuación ${d.score} de 100`}>
                {d.score}
                <span className="text-muted-foreground text-2xl font-normal"> / 100</span>
              </div>
              <div className="space-y-2">
                <p className="font-medium">{d.summary.headline}</p>
                <div className="flex flex-wrap gap-2">
                  {(['critical', 'warning', 'info', 'ok'] as const).map((k) => (
                    <Pill key={k} label={`${d.summary[k]} ${SEVERITY[k].label.toLowerCase()}`} className={SEVERITY[k].pill} />
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          <QualityCharts checks={d.checks} onNavigate={onNavigate} />

          <section className="space-y-3">
            <h2 className="text-lg font-semibold">Requieren atención</h2>
            {open.length === 0 && <p className="text-muted-foreground text-sm">Nada pendiente: todas las revisiones están en orden.</p>}
            {open.map((c) => (
              <CheckRow key={c.id} check={c} onNavigate={onNavigate} />
            ))}
          </section>

          {healthy.length > 0 && (
            <section className="space-y-3">
              <h2 className="text-lg font-semibold">En orden</h2>
              {healthy.map((c) => (
                <CheckRow key={c.id} check={c} />
              ))}
            </section>
          )}
        </>
      )}
    </div>
  )
}
