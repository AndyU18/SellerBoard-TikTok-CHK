import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  notificationsApi,
  type DigestPeriod,
  type NotificationChannel,
  type NotificationStatus,
  type TestResult,
} from '@/lib/notifications'
import { useAsync } from '@/lib/useAsync'

const CHANNELS: { key: NotificationChannel; label: string; vars: string[]; hint: string }[] = [
  {
    key: 'email',
    label: 'Correo',
    vars: ['CHK_SMTP_HOST', 'CHK_SMTP_PORT', 'CHK_SMTP_USER', 'CHK_SMTP_PASSWORD', 'CHK_SMTP_STARTTLS', 'CHK_SMTP_FROM', 'CHK_SMTP_TO'],
    hint: 'Obligatorias: servidor, remitente y al menos un destinatario (CHK_SMTP_TO es una lista JSON).',
  },
  {
    key: 'slack',
    label: 'Slack',
    vars: ['CHK_SLACK_WEBHOOK_URL'],
    hint: 'URL de un webhook entrante de Slack.',
  },
  {
    key: 'webhook',
    label: 'Webhook (WhatsApp vía servicio externo)',
    vars: ['CHK_DIGEST_WEBHOOK_URL'],
    hint: 'Recibe un POST JSON; conéctalo a Twilio, Make o Zapier para enviarlo por WhatsApp.',
  },
]

const KIND: Record<string, string> = { daily: 'Diario', weekly: 'Semanal', test: 'Prueba' }
const CHANNEL_LABEL: Record<string, string> = { email: 'Correo', slack: 'Slack', webhook: 'Webhook' }
const STATUS: Record<NotificationStatus, { label: string; badge: string }> = {
  sent: { label: 'Enviado', badge: 'bg-green-100 text-green-800' },
  failed: { label: 'Falló', badge: 'bg-red-100 text-red-800' },
  skipped: { label: 'Omitido', badge: 'bg-slate-100 text-slate-700' },
}
const WEEKDAYS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']

const when = (iso: string) => new Date(iso).toLocaleString('es-MX', { dateStyle: 'short', timeStyle: 'short' })

function StatusBadge({ status }: { status: NotificationStatus }) {
  return <span className={`rounded px-2 py-0.5 text-xs ${STATUS[status].badge}`}>{STATUS[status].label}</span>
}

export function Notifications() {
  const [version, setVersion] = useState(0)
  const [period, setPeriod] = useState<DigestPeriod>('daily')
  const [confirmed, setConfirmed] = useState(false)
  const [sending, setSending] = useState(false)
  const [outcome, setOutcome] = useState<TestResult | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)

  const status = useAsync(() => notificationsApi.status(), [version])
  const preview = useAsync(() => notificationsApi.preview(period), [period])

  const data = status.data
  const anyChannel = data ? Object.values(data.channels).some(Boolean) : false

  async function sendTest() {
    setSending(true)
    setSendError(null)
    setOutcome(null)
    try {
      setOutcome(await notificationsApi.sendTest())
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e))
    } finally {
      setSending(false)
      setConfirmed(false)
      setVersion((v) => v + 1)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Notificaciones</h1>
        <Button variant="outline" size="sm" onClick={() => setVersion((v) => v + 1)} disabled={status.loading}>
          {status.loading ? 'Actualizando…' : 'Actualizar'}
        </Button>
      </div>

      {status.error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {status.error}
        </div>
      )}

      {data && (
        <div
          className={`rounded-md border p-3 text-sm ${data.enabled ? 'border-green-300 bg-green-50 text-green-900' : 'border-amber-300 bg-amber-50 text-amber-900'}`}
        >
          {data.enabled ? (
            <>
              Resumen automático <strong>activado</strong>: el diario sale a las {String(data.daily_hour).padStart(2, '0')}:00 (
              {data.timezone}) y el semanal los {WEEKDAYS[data.weekly_weekday] ?? '—'}.
              {!anyChannel && ' Pero no hay ningún canal configurado, así que no se envía nada.'}
            </>
          ) : (
            <>
              El resumen automático está <strong>apagado</strong>: no se envía nada a servicios externos. Para activarlo
              define <code>CHK_DIGEST_ENABLED=true</code> y configura un canal.
            </>
          )}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Canales</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          {CHANNELS.map((c) => {
            const on = data?.channels[c.key] ?? false
            return (
              <div key={c.key} className="space-y-2 rounded-md border p-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{c.label}</span>
                  <span
                    className={`rounded px-2 py-0.5 text-xs ${on ? 'bg-green-100 text-green-800' : 'bg-slate-100 text-slate-700'}`}
                  >
                    {on ? 'Configurado' : 'Sin configurar'}
                  </span>
                </div>
                {c.key === 'email' && on && data && (
                  <p className="text-slate-600">
                    {data.email_recipients} {data.email_recipients === 1 ? 'destinatario' : 'destinatarios'}
                  </p>
                )}
                <p className="text-slate-600">{c.hint}</p>
                <p className="break-words font-mono text-xs text-slate-500">{c.vars.join(' · ')}</p>
              </div>
            )
          })}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Vista previa (no se envía)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            {(['daily', 'weekly'] as const).map((p) => (
              <button
                key={p}
                onClick={() => setPeriod(p)}
                className={`rounded-md border px-3 py-1.5 text-sm ${period === p ? 'bg-slate-800 text-white' : 'hover:bg-slate-100'}`}
              >
                {p === 'daily' ? 'Diario' : 'Semanal'}
              </button>
            ))}
          </div>
          {preview.error && (
            <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
              No se pudo generar: {preview.error}
            </div>
          )}
          {preview.loading ? (
            <p className="text-sm text-slate-500">Generando…</p>
          ) : (
            preview.data && (
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-sm dark:bg-slate-900">
                {preview.data.text}
              </pre>
            )
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Enviar prueba</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            <span>Entiendo que se enviarán cifras del negocio a los canales configurados</span>
          </label>
          <Button onClick={sendTest} disabled={!confirmed || sending || !anyChannel}>
            {sending ? 'Enviando…' : 'Enviar prueba'}
          </Button>
          {data && !anyChannel && <p className="text-slate-600">Configura al menos un canal para poder enviar una prueba.</p>}
          {sendError && (
            <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-red-800">
              {sendError}
            </div>
          )}
          {outcome && (
            <ul className="space-y-1">
              {Object.entries(outcome.results).map(([channel, r]) => (
                <li key={channel} className="flex flex-wrap items-center gap-2">
                  <span className="w-24 font-medium">{CHANNEL_LABEL[channel] ?? channel}</span>
                  <StatusBadge status={r.status} />
                  {r.detail && <span className="text-slate-600">{r.detail}</span>}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Registro de envíos (últimos 20)</CardTitle>
        </CardHeader>
        <CardContent>
          {data && data.log.length === 0 ? (
            <p className="text-sm text-slate-500">Todavía no se ha enviado nada.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Fecha</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Canal</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>Detalle</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data?.log ?? []).map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>{when(r.created_at)}</TableCell>
                    <TableCell>{KIND[r.kind] ?? r.kind}</TableCell>
                    <TableCell>{CHANNEL_LABEL[r.channel] ?? r.channel}</TableCell>
                    <TableCell>
                      <StatusBadge status={r.status} />
                    </TableCell>
                    <TableCell className="text-slate-600">{r.detail ?? '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
