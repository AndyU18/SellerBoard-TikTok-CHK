import { useMemo, useState } from 'react'
import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer } from 'recharts'

import { HBarChart, type HBarRow } from '@/components/analytics/HBarChart'
import { Callout, ErrorAlert, Hint, Kpi, PILL, Pill, Segmented } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { count } from '@/lib/format'
import { daysFromPreset } from '@/lib/redo'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'
import { type QualityComponent, type QualityComponentKey, walmartQuality } from '@/lib/walmartQuality'

import { PageTitle } from './walmartUi'

const QUALITY_TARGET = 100
/** Desde este puntaje el componente se considera bueno (verde). */
const GOOD_SCORE = 90
/** Desde este puntaje y por debajo de GOOD_SCORE es aceptable (ámbar); menos es crítico (rojo). */
const WARN_SCORE = 70
const OVERALL_KEY = 'listing_quality'
type FocusKey = QualityComponentKey | typeof OVERALL_KEY

type Light = { label: string; color: string; pill: string }
const LIGHT_GOOD: Light = { label: 'Bueno', color: COLOR.good, pill: PILL.green }
const LIGHT_WARN: Light = { label: 'Mejorable', color: COLOR.amber, pill: PILL.amber }
const LIGHT_BAD: Light = { label: 'Crítico', color: COLOR.bad, pill: PILL.red }
const LIGHT_NONE: Light = { label: 'Sin dato', color: COLOR.neutral, pill: PILL.slate }

/** Semáforo de un puntaje de 0 a 100. */
function light(score: number | null | undefined): Light {
  if (score == null) return LIGHT_NONE
  if (score >= GOOD_SCORE) return LIGHT_GOOD
  if (score >= WARN_SCORE) return LIGHT_WARN
  return LIGHT_BAD
}

/** Qué mejora cada componente (texto de ayuda para el equipo de listados). */
const IMPROVES: Record<QualityComponentKey, string> = {
  offer_score: 'Mejora con ofertas completas y vigentes: precio, existencias y envío configurados en cada artículo.',
  rating_review_score: 'Sube con más reseñas y mejores calificaciones: pide opiniones tras la entrega y resuelve rápido las quejas.',
  content_score: 'Sube con títulos, descripciones, imágenes y atributos completos y de buena calidad.',
  price_score: 'Mejora al acercar el precio al de la competencia en el mismo artículo (revisa el repricer).',
  shipping_score: 'Mejora con envío rápido y gratuito, plantillas de envío correctas y WFS en los SKU principales.',
  transactibility_score: 'Sube cuando el artículo se puede comprar sin fricciones: publicado, con stock y sin bloqueos.',
}

const pts = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(1))
const shortDate = (v: string | number) => String(v).slice(5)

function Delta({ value }: { value: number | null }) {
  if (value == null) return <span className="text-muted-foreground text-sm">Sin foto anterior para comparar</span>
  const up = value >= 0
  return (
    <span className={`text-sm font-medium ${value === 0 ? 'text-muted-foreground' : up ? 'text-emerald-600' : 'text-red-600'}`}>
      {value === 0 ? '=' : up ? '▲' : '▼'} {Math.abs(value).toFixed(1)} pts vs la foto anterior
    </span>
  )
}

function OverallScore({ score, delta, date }: { score: number | null; delta: number | null; date: string }) {
  const l = light(score)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Puntaje general del listado</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex items-baseline gap-2">
          <span className="text-5xl font-bold tabular-nums" style={{ color: l.color }}>
            {pts(score)}
          </span>
          <span className="text-muted-foreground text-sm">/ {QUALITY_TARGET}</span>
          <Pill label={l.label} className={l.pill} />
        </div>
        <div>
          <Delta value={delta} />
        </div>
        <div className="text-muted-foreground text-xs">Foto del {date}</div>
      </CardContent>
    </Card>
  )
}

function QualityRadar({ components }: { components: QualityComponent[] }) {
  const data = components.map((c) => ({ label: c.label, score: c.score ?? 0, target: QUALITY_TARGET }))
  return (
    <div role="img" aria-label="Radar de los componentes de calidad frente a la meta de 100" className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={data} outerRadius="70%">
          <PolarGrid />
          <PolarAngleAxis dataKey="label" tick={{ fontSize: 11 }} />
          <PolarRadiusAxis angle={90} domain={[0, QUALITY_TARGET]} tick={{ fontSize: 10 }} />
          <Radar name="Meta" dataKey="target" stroke={COLOR.neutral} fill="none" strokeDasharray="4 4" />
          <Radar name="Puntaje" dataKey="score" stroke={COLOR.blue} fill={COLOR.blue} fillOpacity={0.3} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}

export function WalmartListingQuality() {
  const { preset } = useScope()
  const days = daysFromPreset(preset)
  const scope = useScopeDescription()
  const state = useAsync(() => walmartQuality.listingQuality(days), [days])
  const d = state.data
  const latest = d?.latest ?? null
  const [focus, setFocus] = useState<FocusKey>(OVERALL_KEY)

  const components = useMemo(() => latest?.components ?? [], [latest])
  const gapRows = useMemo<HBarRow[]>(
    () => components.map((c) => ({ id: c.key, label: c.label, gap: c.gap_to_100 ?? QUALITY_TARGET, score: c.score })),
    [components],
  )
  const scoreByRow = useMemo(() => new Map(components.map((c) => [c.key, c.score])), [components])

  const focusLabel = components.find((c) => c.key === focus)?.label
  const series = useMemo<ComboSeries[]>(() => {
    const base: ComboSeries = { key: OVERALL_KEY, label: 'Puntaje general', type: 'line', color: COLOR.blue, format: pts }
    if (focus === OVERALL_KEY) return [base]
    return [base, { key: focus, label: focusLabel ?? focus, type: 'line', color: COLOR.amber, format: pts }]
  }, [focus, focusLabel])
  const history = useMemo(() => (d?.history ?? []).filter((h) => h.listing_quality != null), [d?.history])
  const focusOptions: { value: FocusKey; label: string }[] = [
    { value: OVERALL_KEY, label: 'Solo el general' },
    ...components.map((c) => ({ value: c.key, label: c.label })),
  ]

  const defects = latest?.defects
  const empty = (msg: string) => <EmptyChart>{state.loading ? 'Cargando…' : msg}</EmptyChart>

  return (
    <div className="space-y-6">
      <PageTitle
        title="Calidad del listado"
        scope={scope}
        note={d ? `Historial de los últimos ${d.days} días (período global)` : undefined}
      />

      <ErrorAlert message={state.error} />
      {state.loading && !d && <div className="text-muted-foreground text-sm">Cargando…</div>}

      {d && !latest && <Callout tone="info">Todavía no hay una foto de calidad del listado de Walmart. Aparecerá después de la próxima sincronización.</Callout>}

      {d && !d.item_level.available && (
        <Callout tone="info">
          Calidad por artículo no disponible. {d.item_level.reason ?? 'Walmart no la entrega a esta integración.'}
        </Callout>
      )}

      {latest && (
        <>
          <div className="grid gap-4 md:grid-cols-3">
            <OverallScore score={latest.listing_quality} delta={latest.change_vs_previous} date={latest.snapshot_date} />
            <Kpi
              title="Defectos posteriores a la compra"
              value={count(defects?.item_defect_count)}
              hint="artículos con defecto (devoluciones, quejas, cancelaciones)"
              className={(defects?.item_defect_count ?? 0) > 0 ? 'text-red-600' : ''}
            />
            <Kpi
              title="Proporción de defectos"
              value={defects?.defect_ratio == null ? '—' : `${(defects.defect_ratio * 100).toFixed(1)}%`}
              hint="del total de artículos vendidos"
              className={(defects?.defect_ratio ?? 0) > 0 ? 'text-amber-600' : ''}
            />
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="Componentes frente a la meta de 100"
              subtitle="Cuanto más cerca del borde exterior, mejor. La línea punteada es la meta."
              filename="walmart-calidad-radar"
              csv={components.map((c) => ({ Componente: c.label, Puntaje: c.score, 'Brecha a 100': c.gap_to_100 }))}
            >
              <QualityRadar components={components} />
            </ChartCard>

            <ChartCard
              title="Brecha a 100 por componente"
              subtitle={`Puntos que faltan para llegar a ${QUALITY_TARGET}. Verde desde ${GOOD_SCORE}, ámbar desde ${WARN_SCORE}, rojo por debajo.`}
              filename="walmart-calidad-brecha"
              csv={components.map((c) => ({ Componente: c.label, Puntaje: c.score, 'Brecha a 100': c.gap_to_100 }))}
            >
              <HBarChart
                rows={gapRows}
                series={[{ key: 'gap', label: 'Brecha a 100' }]}
                format={pts}
                labelWidth={150}
                rowColor={(row) => light(scoreByRow.get(row.id as QualityComponentKey)).color}
                selectedId={focus === OVERALL_KEY ? null : focus}
                onRowClick={(row) => setFocus(row.id as QualityComponentKey)}
              />
            </ChartCard>
          </div>

          <ChartCard
            title="Historial diario del puntaje"
            subtitle="Un clic en una barra de brecha compara ese componente con el puntaje general."
            filename="walmart-calidad-historial"
            csv={history.map((h) => ({ Fecha: h.date, 'Puntaje general': h.listing_quality, ...Object.fromEntries(components.map((c) => [c.label, h[c.key]])) }))}
          >
            <div className="mb-3">
              <Segmented value={focus} options={focusOptions} onChange={setFocus} label="Componente a comparar con el puntaje general" />
            </div>
            {history.length === 0 ? (
              empty('Sin historial en este período.')
            ) : (
              <ComboChart data={history} xKey="date" series={series} xFormat={shortDate} leftFormat={pts} height={300} />
            )}
            <Hint>
              Walmart solo entrega los defectos de la última foto, no por día; por eso el historial muestra únicamente los puntajes.
            </Hint>
          </ChartCard>

          <Card>
            <CardHeader>
              <CardTitle>Qué mejora cada componente</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="grid gap-3 md:grid-cols-2">
                {components.map((c) => {
                  const l = light(c.score)
                  return (
                    <li key={c.key} className="rounded-md border p-3 text-sm">
                      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                        <span className="font-medium">{c.label}</span>
                        <span className="flex items-center gap-2 tabular-nums">
                          {pts(c.score)}
                          <Pill label={l.label} className={l.pill} />
                        </span>
                      </div>
                      <p className="text-muted-foreground text-xs">
                        {c.gap_to_100 != null && c.gap_to_100 > 0 ? `Faltan ${pts(c.gap_to_100)} puntos. ` : ''}
                        {IMPROVES[c.key]}
                      </p>
                    </li>
                  )
                })}
              </ul>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
