import { useMemo } from 'react'

import { Callout, ErrorAlert, Kpi, Pill, PILL } from '@/components/analytics/ui'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { count } from '@/lib/format'
import { tiktokMoney } from '@/lib/tiktokMoney'
import { useAsync } from '@/lib/useAsync'

import { PageTitle } from '../walmart/walmartUi'
import { shortDate } from './tiktokMoneyFormat'
import { EmptyRows } from './tiktokMoneyUi'
import { guideFor } from './tiktokWebhookEvents'

const PURPOSE =
  'Un webhook es un aviso en vivo: TikTok Shop llama a una dirección tuya cada vez que ocurre algo (una orden, una devolución, un paquete), en lugar de que la app consulte cada 6 horas.'
const READ_ONLY_NOTE =
  'Esta app es de solo lectura: no crea ni borra suscripciones. Se activan en el panel de TikTok Shop Partner Center, indicando la dirección que recibirá los avisos.'

function SubscribedPill({ subscribed }: { subscribed: boolean }) {
  return <Pill label={subscribed ? 'Suscrito' : 'Sin suscribir'} className={subscribed ? PILL.green : PILL.amber} />
}

export function TikTokWebhooks() {
  const description = useScopeDescription()
  const report = useAsync(() => tiktokMoney.webhooks(), [])
  const data = report.data

  const events = useMemo(
    () =>
      (data?.documented_events ?? [])
        .map((e) => ({ ...e, ...guideFor(e.event_type) }))
        .sort((a, b) => Number(b.recommended) - Number(a.recommended) || a.group.localeCompare(b.group)),
    [data],
  )
  const recommended = events.filter((e) => e.recommended)
  const recommendedPending = recommended.filter((e) => !e.subscribed).length

  return (
    <div className="space-y-6">
      <PageTitle title="Webhooks" scope={description} note="Avisos en vivo de TikTok Shop y cuáles faltan por suscribir." />
      <ErrorAlert message={report.error} />

      <Callout tone="info">
        <p>{PURPOSE}</p>
        <p className="mt-1">{READ_ONLY_NOTE}</p>
      </Callout>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi title="Suscripciones activas" value={data ? count(data.subscriptions.length) : '—'} hint="eventos que ya avisan en vivo" />
        <Kpi title="Eventos disponibles" value={data ? count(events.length) : '—'} hint="documentados por TikTok Shop" />
        <Kpi title="Sin suscribir" value={data ? count(data.unsubscribed.length) : '—'} hint="aún se leen por consulta periódica" />
        <Kpi
          title="Recomendados pendientes"
          value={data ? count(recommendedPending) : '—'}
          hint={data ? `de ${count(recommended.length)} recomendados` : undefined}
          className={recommendedPending > 0 ? 'text-amber-600' : ''}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Suscripciones actuales</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {data && data.subscriptions.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Evento</TableHead>
                  <TableHead>Dirección de aviso</TableHead>
                  <TableHead>Creada</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.subscriptions.map((s) => (
                  <TableRow key={s.event_type}>
                    <TableCell className="font-mono text-xs">{s.event_type}</TableCell>
                    <TableCell className="text-xs break-all">{s.address ?? '—'}</TableCell>
                    <TableCell className="whitespace-nowrap">{shortDate(s.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyRows loading={report.loading} message="No hay suscripciones: hoy todo se actualiza consultando a TikTok Shop cada 6 horas." />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Eventos recomendados y por qué</CardTitle>
          <p className="text-muted-foreground text-xs">Órdenes, devoluciones, paquetes y productos son los que más mueven tu utilidad. El resto es opcional.</p>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {events.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Evento</TableHead>
                  <TableHead>Grupo</TableHead>
                  <TableHead>Para qué sirve</TableHead>
                  <TableHead>Prioridad</TableHead>
                  <TableHead>Estado</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {events.map((e) => (
                  <TableRow key={e.event_type}>
                    <TableCell>
                      <div className="font-medium">{e.title}</div>
                      <div className="text-muted-foreground font-mono text-xs">{e.event_type}</div>
                    </TableCell>
                    <TableCell className="whitespace-nowrap">{e.group}</TableCell>
                    <TableCell className="min-w-64 text-sm">{e.why}</TableCell>
                    <TableCell>
                      <Pill label={e.recommended ? 'Recomendado' : 'Opcional'} className={e.recommended ? PILL.blue : PILL.slate} />
                    </TableCell>
                    <TableCell>
                      <SubscribedPill subscribed={e.subscribed} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyRows loading={report.loading} message="No hay eventos documentados disponibles." />
          )}
          {data && <p className="text-muted-foreground mt-3 text-xs">{data.documented_events_note}</p>}
        </CardContent>
      </Card>
    </div>
  )
}
