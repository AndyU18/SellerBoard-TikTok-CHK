import { useMemo } from 'react'

import { Callout, ErrorAlert, Kpi } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { axisNumber, ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { HeatGrid } from '@/components/charts/HeatGrid'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count } from '@/lib/format'
import { tiktokService, type CsOfficialTotals, type CsResponseSummary, type CsSlot } from '@/lib/tiktokService'
import { useAsync } from '@/lib/useAsync'

import { NO_VALUE } from './tiktokFormat'
import { durationText, pctText, secondsToMinutes, useWindowDays } from './tiktokServiceFormat'
import { OfficialBadge, ServiceHeader } from './tiktokServiceUi'

/** Con menos respuestas que esto, una franja no sirve para sacar conclusiones. */
const MIN_SLOT_SAMPLE = 5
const HOURS_LABELS = Array.from({ length: 24 }, (_, h) => `${h}h`)
const minText = (v: number) => `${v} min`

const SLOT_SERIES: ComboSeries[] = [
  { key: 'conversations', label: 'Conversaciones con pregunta', type: 'bar', color: COLOR.blue },
  { key: 'median_minutes', label: 'Respuesta mediana (min)', type: 'line', axis: 'right', color: COLOR.bad, format: (v) => `${v.toFixed(1)} min` },
]

type SlotRow = CsSlot & { label: string; median_minutes: number | null }

const withMinutes = <T extends CsSlot>(slots: T[], label: (s: T) => string): SlotRow[] =>
  slots.map((s) => ({ ...s, label: label(s), median_minutes: secondsToMinutes(s.median_seconds) }))

/** Franja más rápida y más lenta entre las que tienen respuestas; avisa si la muestra es baja. */
function BestWorst({ title, rows }: { title: string; rows: SlotRow[] }) {
  const ranked = rows.filter((r) => r.median_seconds != null).sort((a, b) => (a.median_seconds ?? 0) - (b.median_seconds ?? 0))
  const best = ranked[0]
  const worst = ranked.length > 1 ? ranked[ranked.length - 1] : undefined
  const low = [best, worst].some((r) => r && r.responded < MIN_SLOT_SAMPLE)
  const line = (kind: string, r: SlotRow | undefined) =>
    r ? (
      <li>
        <span className="text-muted-foreground">{kind}: </span>
        <strong>{r.label}</strong> · mediana {durationText(r.median_seconds)} · {count(r.responded)} respuestas
        {r.responded < MIN_SLOT_SAMPLE && <span className="text-amber-700"> (muestra baja)</span>}
      </li>
    ) : null
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {best ? (
          <ul className="space-y-1">
            {line('Más rápida', best)}
            {line('Más lenta', worst)}
          </ul>
        ) : (
          <p className="text-muted-foreground">Aún no hay respuestas para comparar franjas.</p>
        )}
        {low && <p className="text-xs text-amber-800">Menos de {MIN_SLOT_SAMPLE} respuestas en alguna franja: tómalo como una pista, no como una conclusión.</p>}
      </CardContent>
    </Card>
  )
}

function Comparison({ own, official }: { own: CsResponseSummary; official: CsOfficialTotals | null }) {
  const hasOfficial = official != null && official.days_with_data > 0
  const rows = [
    { metric: 'Tiempo de respuesta', own: durationText(own.avg_seconds), official: hasOfficial ? durationText(official.response_seconds) : NO_VALUE, note: 'Media de la primera respuesta humana' },
    { metric: '% de respuesta', own: pctText(own.response_rate_pct), official: hasOfficial ? pctText(official.response_percentage) : NO_VALUE, note: 'Conversaciones con mensaje de comprador que recibieron respuesta' },
  ]
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <div>
          <CardTitle className="text-base">Calculado por la app vs oficial de TikTok</CardTitle>
          <p className="text-muted-foreground mt-1 text-xs">Pueden diferir: TikTok mide sesiones de servicio y la app mide la primera respuesta humana de cada conversación.</p>
        </div>
        <OfficialBadge />
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Métrica</TableHead>
              <TableHead className="text-right">Calculado (app)</TableHead>
              <TableHead className="text-right">Oficial (TikTok)</TableHead>
              <TableHead>Qué mide</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.metric}>
                <TableCell className="font-medium">{r.metric}</TableCell>
                <TableCell className="text-right">{r.own}</TableCell>
                <TableCell className="text-right">{r.official}</TableCell>
                <TableCell className="text-muted-foreground text-xs">{r.note}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

/** Tiempos de respuesta del servicio al cliente: rangos, horas del día, días de la semana y mejores franjas. */
export function TikTokCsResponseTimes() {
  const days = useWindowDays()
  const data = useAsync(() => tiktokService.responseTimes(days), [days])
  const overview = useAsync(() => tiktokService.overview(days), [days])
  const report = data.data

  const hours = useMemo(() => withMinutes(report?.by_hour ?? [], (s) => `${s.hour}h`), [report])
  const weekdays = useMemo(() => withMinutes(report?.by_weekday ?? [], (s) => s.weekday), [report])
  const heat = useMemo(() => [(report?.by_hour ?? []).map((s) => secondsToMinutes(s.median_seconds) ?? 0)], [report])
  const loading = data.loading && !report
  const summary = report?.summary

  return (
    <div className="space-y-6 p-6">
      <ServiceHeader
        title="Tiempos de respuesta"
        note={`Primera respuesta humana a conversaciones con mensaje de comprador, últimos ${days} días${report ? `. Horas y días en la zona ${report.timezone}` : ''}.`}
      />

      <ErrorAlert message={data.error} />
      {loading && <EmptyChart>Cargando…</EmptyChart>}
      {summary && summary.conversations_with_buyer_message === 0 && <Callout tone="info">No hay conversaciones con mensaje de comprador en este período.</Callout>}

      {report && summary && summary.conversations_with_buyer_message > 0 && (
        <>
          <section aria-label="Percentiles" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
            <Kpi title="Mediana" value={durationText(summary.median_seconds)} />
            <Kpi title="Media" value={durationText(summary.avg_seconds)} />
            <Kpi title="p90" value={durationText(summary.p90_seconds)} hint="9 de cada 10 se responden antes" />
            <Kpi title="En 1 h" value={pctText(summary.within_1h_pct)} />
            <Kpi title="En 24 h" value={pctText(summary.within_24h_pct)} />
            <Kpi title="Con respuesta" value={`${count(summary.responded)} de ${count(summary.conversations_with_buyer_message)}`} hint={pctText(summary.response_rate_pct)} />
          </section>

          <Comparison own={summary} official={overview.data?.official ?? null} />

          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="Rangos de tiempo de respuesta"
              subtitle="Cuántas conversaciones se respondieron en cada rango (y cuántas siguen sin respuesta)."
              csv={report.distribution.map((d) => ({ rango: d.bucket, conversaciones: d.count }))}
              filename="tiktok-servicio-rangos-de-respuesta"
            >
              <ComboChart
                data={report.distribution}
                xKey="bucket"
                series={[{ key: 'count', label: 'Conversaciones', type: 'bar', color: COLOR.blue }]}
                leftFormat={axisNumber}
                height={260}
              />
            </ChartCard>

            <ChartCard
              title="Por día de la semana"
              subtitle="Según el día en que escribió el comprador."
              csv={weekdays.map((d) => ({ dia: d.label, conversaciones: d.conversations, respondidas: d.responded, mediana_min: d.median_minutes }))}
              filename="tiktok-servicio-por-dia-de-la-semana"
            >
              <ComboChart data={weekdays} xKey="label" series={SLOT_SERIES} leftFormat={axisNumber} rightFormat={minText} height={260} />
            </ChartCard>
          </div>

          <ChartCard
            title="Por hora del día"
            subtitle="Según la hora en que escribió el comprador."
            csv={hours.map((h) => ({ hora: h.label, conversaciones: h.conversations, respondidas: h.responded, mediana_min: h.median_minutes }))}
            filename="tiktok-servicio-por-hora"
          >
            <ComboChart data={hours} xKey="label" series={SLOT_SERIES} leftFormat={axisNumber} rightFormat={minText} height={280} />
          </ChartCard>

          <ChartCard
            title="Mapa de calor: tiempo mediano por hora"
            subtitle="Más oscuro = respuesta más lenta. Las horas sin respuestas aparecen como 0 min."
          >
            <HeatGrid rowLabels={['Mediana']} colLabels={HOURS_LABELS} values={heat} format={(v) => `${v.toFixed(1)}`} unit="min" />
          </ChartCard>

          <div className="grid gap-4 xl:grid-cols-2">
            <BestWorst title="Mejor y peor hora" rows={hours} />
            <BestWorst title="Mejor y peor día" rows={weekdays} />
          </div>
        </>
      )}
    </div>
  )
}
