import { ExternalLink } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Callout, ErrorAlert, Kpi } from '@/components/analytics/ui'
import { ComboChart, type ComboSeries } from '@/components/charts/ComboChart'
import { axisNumber, ChartCard, COLOR, EmptyChart } from '@/components/charts/kit'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count } from '@/lib/format'
import { tiktokService, type CsAgents, type CsConversation } from '@/lib/tiktokService'
import { useAsync } from '@/lib/useAsync'

import { NO_VALUE } from './tiktokFormat'
import { categoryLabel, durationText, hoursText, pctText, SELLER_CENTER_URL, waitTone } from './tiktokServiceFormat'
import { CopyId, OfficialBadge, ServiceHeader } from './tiktokServiceUi'

const OLDEST_SHOWN = 5
const LIST_SHOWN = 25

/** Rangos de antigüedad en horas; los nombres son los que devuelve el backend. */
const BUCKET_RANGES: Record<string, readonly [number, number]> = {
  '<1 h': [0, 1],
  '1-6 h': [1, 6],
  '6-24 h': [6, 24],
  '1-3 d': [24, 72],
  '>3 d': [72, Infinity],
}

const SERIES: ComboSeries[] = [
  { key: 'count', label: 'Sin responder', type: 'bar', color: COLOR.bad },
  { key: 'unread', label: 'Mensajes no leídos', type: 'bar', color: COLOR.amber },
]

const inBucket = (c: CsConversation, bucket: string | null) => {
  if (!bucket) return true
  const range = BUCKET_RANGES[bucket]
  return range != null && c.wait_hours != null && c.wait_hours >= range[0] && c.wait_hours < range[1]
}

/** Las conversaciones más antiguas sin responder: solo id corto y espera. */
function DoNow({ rows }: { rows: CsConversation[] }) {
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <div>
          <CardTitle className="text-base">Qué hacer ahora</CardTitle>
          <p className="text-muted-foreground mt-1 text-xs">Responde primero las que más llevan esperando. Copia el id y búscala en el panel de TikTok Shop.</p>
        </div>
        <a
          href={SELLER_CENTER_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1 rounded border px-3 py-1.5 text-sm hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none"
        >
          Abrir en TikTok Shop (seller-us.tiktok.com) <ExternalLink className="size-3.5" aria-hidden />
        </a>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-green-700">No hay conversaciones esperando al vendedor.</p>
        ) : (
          <ol className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {rows.map((c, i) => (
              <li key={c.conversation_id} className="flex items-center justify-between gap-2 rounded-md border p-3 text-sm">
                <span className="flex items-center gap-2">
                  <span className="text-muted-foreground text-xs">{i + 1}.</span>
                  <CopyId id={c.conversation_id} />
                </span>
                <span className={`font-medium ${waitTone(c.wait_hours)}`}>{hoursText(c.wait_hours)}</span>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}

function AgentsCard({ loading, error, agents }: { loading: boolean; error: string | null; agents: CsAgents | null }) {
  const official = agents?.official_last_7_days
  const status = agents?.status === 'accepting_chats' ? 'Aceptan chats' : agents?.status === 'not_accepting_chats' ? 'No aceptan chats' : agents ? 'Sin información' : loading ? 'Cargando…' : NO_VALUE
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="text-base">Agentes</CardTitle>
        {official && official.days_with_data > 0 && <OfficialBadge />}
      </CardHeader>
      <CardContent className="space-y-1 text-sm">
        <ErrorAlert message={error} />
        <div className={`text-lg font-semibold ${agents?.status === 'accepting_chats' ? 'text-green-700' : agents?.status === 'not_accepting_chats' ? 'text-red-700' : ''}`}>{status}</div>
        {agents?.status === 'not_accepting_chats' && <p className="text-xs text-red-700">TikTok no asigna chats nuevos a los agentes: revisa su configuración en el panel de TikTok Shop.</p>}
        {official && official.days_with_data > 0 && (
          <p className="text-muted-foreground text-xs">
            Últimos 7 días (TikTok): {count(official.sessions)} sesiones · respuesta {pctText(official.response_percentage)} en {durationText(official.response_seconds)} · satisfacción{' '}
            {pctText(official.satisfaction_percentage)}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

/** Conversaciones sin responder por antigüedad, con la lista de lo más urgente. */
export function TikTokCsBacklog() {
  const backlog = useAsync(() => tiktokService.backlog(), [])
  const agents = useAsync(() => tiktokService.agents(), [])
  const waiting = useAsync(() => tiktokService.conversations({ status: 'waiting_seller' }), [])
  const [bucket, setBucket] = useState<string | null>(null)

  const report = backlog.data
  const waitingRows = useMemo(() => waiting.data?.items ?? [], [waiting.data])
  const filtered = useMemo(() => waitingRows.filter((c) => inBucket(c, bucket)), [waitingRows, bucket])
  const loading = backlog.loading && !report

  return (
    <div className="space-y-6 p-6">
      <ServiceHeader
        title="Pendientes de respuesta"
        note="Conversaciones donde el comprador escribió después de la última respuesta humana. La antigüedad se cuenta desde el mensaje más viejo sin responder."
      />

      <ErrorAlert message={backlog.error ?? waiting.error} />
      {loading && <EmptyChart>Cargando…</EmptyChart>}

      {report && (
        <>
          <section aria-label="Indicadores" className="grid gap-3 sm:grid-cols-2">
            <Kpi title="Sin responder" value={count(report.total)} className={report.total > 0 ? 'text-red-700' : 'text-green-700'} />
            <Kpi title="La más antigua" value={hoursText(report.oldest_wait_hours)} className={waitTone(report.oldest_wait_hours)} />
          </section>

          {report.total === 0 ? (
            <Callout tone="info">No hay conversaciones esperando al vendedor.</Callout>
          ) : (
            <ChartCard
              title="Antigüedad de lo pendiente"
              subtitle="Haz clic en una barra para filtrar la lista de abajo."
              csv={report.buckets.map((b) => ({ antiguedad: b.bucket, sin_responder: b.count, no_leidos: b.unread }))}
              filename="tiktok-servicio-pendientes"
            >
              <ComboChart
                data={report.buckets}
                xKey="bucket"
                series={SERIES}
                leftFormat={axisNumber}
                height={260}
                onPointClick={(row) => setBucket((cur) => (cur === row.bucket ? null : String(row.bucket)))}
              />
            </ChartCard>
          )}

          <div className="grid gap-4 xl:grid-cols-3">
            <div className="xl:col-span-2">
              <DoNow rows={waitingRows.slice(0, OLDEST_SHOWN)} />
            </div>
            <AgentsCard loading={agents.loading} error={agents.error} agents={agents.data} />
          </div>

          <Card>
            <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
              <CardTitle className="text-base">
                Pendientes{bucket ? ` de ${bucket}` : ''} ({count(filtered.length)})
              </CardTitle>
              {bucket && (
                <button type="button" onClick={() => setBucket(null)} className="rounded border px-3 py-1.5 text-xs hover:bg-slate-100">
                  Quitar filtro
                </button>
              )}
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Conversación</TableHead>
                    <TableHead>Categoría</TableHead>
                    <TableHead className="text-right">Espera</TableHead>
                    <TableHead className="text-right">No leídos</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.slice(0, LIST_SHOWN).map((c) => (
                    <TableRow key={c.conversation_id}>
                      <TableCell>
                        <CopyId id={c.conversation_id} />
                      </TableCell>
                      <TableCell>{categoryLabel(c.category)}</TableCell>
                      <TableCell className={`text-right font-medium whitespace-nowrap ${waitTone(c.wait_hours)}`}>{hoursText(c.wait_hours)}</TableCell>
                      <TableCell className="text-right">{count(c.unread_count)}</TableCell>
                    </TableRow>
                  ))}
                  {filtered.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={4} className="text-muted-foreground text-center">
                        {waiting.loading ? 'Cargando…' : 'No hay conversaciones en este rango.'}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
              {filtered.length > LIST_SHOWN && <p className="text-muted-foreground mt-2 text-xs">Se muestran las {LIST_SHOWN} con más espera. La lista completa está en Conversaciones.</p>}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
