import { useMemo } from 'react'

import { Callout, ErrorAlert } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { Donut, type DonutSlice } from '@/components/charts/Donut'
import { axisNumber, ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count } from '@/lib/format'
import { tiktokService } from '@/lib/tiktokService'
import { useAsync } from '@/lib/useAsync'

import { categoryLabel, durationText, pctText, secondsToMinutes, useWindowDays } from './tiktokServiceFormat'
import { ServiceHeader } from './tiktokServiceUi'

const VOLUME_SERIES: ComboSeries[] = [
  { key: 'conversations', label: 'Conversaciones', type: 'bar', color: COLOR.blue },
  { key: 'unresolved', label: 'Sin responder', type: 'bar', color: COLOR.bad },
]
const RESPONSE_SERIES: ComboSeries[] = [
  { key: 'median_minutes', label: 'Mediana (min)', type: 'bar', color: COLOR.good, format: (v) => `${v.toFixed(1)} min` },
  { key: 'avg_minutes', label: 'Media (min)', type: 'bar', color: COLOR.amber, format: (v) => `${v.toFixed(1)} min` },
]

function HowItWorks() {
  return (
    <Callout tone="info">
      <strong>Cómo se clasifica:</strong> la categoría se deduce con palabras clave de lo que escribe el comprador. La
      lectura ocurre en memoria al sincronizar y el texto se descarta: solo se guarda la categoría resultante. Es una estimación y puede equivocarse; lo que no encaja en ninguna queda
      en "Otros".
    </Callout>
  )
}

/** Volumen, tiempo de respuesta y conversaciones sin responder por categoría. */
export function TikTokCsCategories() {
  const days = useWindowDays()
  const data = useAsync(() => tiktokService.categories(days), [days])
  const report = data.data

  const rows = useMemo(
    () =>
      (report?.categories ?? []).map((c) => ({
        ...c,
        label: categoryLabel(c.category),
        median_minutes: secondsToMinutes(c.median_response_seconds),
        avg_minutes: secondsToMinutes(c.avg_response_seconds),
      })),
    [report],
  )
  const slices = useMemo<DonutSlice[]>(() => rows.map((r) => ({ id: r.category, label: r.label, value: r.conversations })), [rows])
  const loading = data.loading && !report
  const empty = report != null && report.total_with_buyer_message === 0

  return (
    <div className="space-y-6 p-6">
      <ServiceHeader
        title="Categorías de conversación"
        note={`Conversaciones con mensaje de comprador de los últimos ${days} días; las que solo tienen bot o sistema no se clasifican.`}
      />

      <ErrorAlert message={data.error} />
      {loading && <EmptyChart>Cargando…</EmptyChart>}
      {empty && <Callout tone="info">No hay conversaciones con mensaje de comprador en este período.</Callout>}

      {report && !empty && (
        <>
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard
              title="Reparto por categoría"
              subtitle={`${count(report.total_with_buyer_message)} conversaciones con mensaje de comprador.`}
              csv={rows.map((r) => ({ categoria: r.label, conversaciones: r.conversations, porcentaje: r.share_pct }))}
              filename="tiktok-servicio-reparto-categorias"
            >
              <Donut slices={slices} format={count} centerLabel="conversaciones" />
            </ChartCard>

            <ChartCard
              title="Volumen y sin responder"
              subtitle="Sin responder = esperan al vendedor ahora mismo."
              csv={rows.map((r) => ({ categoria: r.label, conversaciones: r.conversations, sin_responder: r.unresolved }))}
              filename="tiktok-servicio-volumen-categorias"
            >
              <ComboChart data={rows} xKey="label" series={VOLUME_SERIES} leftFormat={axisNumber} height={240} />
            </ChartCard>
          </div>

          <ChartCard
            title="Tiempo de respuesta por categoría"
            subtitle="Primera respuesta humana, en minutos; solo conversaciones respondidas."
            csv={rows.map((r) => ({ categoria: r.label, respondidas: r.responded, mediana_min: r.median_minutes, media_min: r.avg_minutes }))}
            filename="tiktok-servicio-respuesta-categorias"
          >
            <ComboChart data={rows} xKey="label" series={RESPONSE_SERIES} leftFormat={(v) => `${v}`} height={260} />
          </ChartCard>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Detalle por categoría</CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Categoría</TableHead>
                    <TableHead className="text-right">Conversaciones</TableHead>
                    <TableHead className="text-right">% del total</TableHead>
                    <TableHead className="text-right">Respondidas</TableHead>
                    <TableHead className="text-right">Mediana</TableHead>
                    <TableHead className="text-right">Media</TableHead>
                    <TableHead className="text-right">Sin responder</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r) => (
                    <TableRow key={r.category}>
                      <TableCell className="font-medium">{r.label}</TableCell>
                      <TableCell className="text-right">{count(r.conversations)}</TableCell>
                      <TableCell className="text-right">{pctText(r.share_pct)}</TableCell>
                      <TableCell className="text-right">{count(r.responded)}</TableCell>
                      <TableCell className="text-right">{durationText(r.median_response_seconds)}</TableCell>
                      <TableCell className="text-right">{durationText(r.avg_response_seconds)}</TableCell>
                      <TableCell className={`text-right ${r.unresolved > 0 ? 'font-medium text-red-700' : ''}`}>{count(r.unresolved)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}

      <HowItWorks />
    </div>
  )
}
