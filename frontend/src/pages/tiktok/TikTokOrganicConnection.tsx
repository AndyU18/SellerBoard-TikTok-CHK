import { CheckCircle2, Circle, Copy, ExternalLink, Link2Off, RefreshCw } from 'lucide-react'
import { useState } from 'react'

import { Callout, ErrorAlert, Pill, PILL } from '@/components/analytics/ui'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { parseCallback, tiktokOrganic, type OrganicAuthorizeUrl, type OrganicStatus } from '@/lib/tiktokOrganic'
import { useAsync } from '@/lib/useAsync'

/** Permisos que debe tener activados la app en el portal de TikTok for Developers. */
const REQUIRED_SCOPES: { scope: string; why: string }[] = [
  { scope: 'user.info.basic', why: 'nombre y foto de la cuenta' },
  { scope: 'user.info.profile', why: 'usuario, biografía y enlace del perfil' },
  { scope: 'user.info.stats', why: 'seguidores, siguiendo, likes y número de videos' },
  { scope: 'video.list', why: 'lista de videos y sus vistas, likes, comentarios y compartidos' },
]

const dateTime = (iso: string | null) => (iso ? new Date(iso).toLocaleString('es-US', { dateStyle: 'medium', timeStyle: 'short' }) : '—')

function Step({ n, done, title, children }: { n: number; done?: boolean; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="mt-0.5 shrink-0">{done ? <CheckCircle2 className="size-5 text-emerald-600" aria-label="Hecho" /> : <Circle className="text-muted-foreground size-5" aria-hidden="true" />}</span>
      <div className="min-w-0 flex-1 space-y-2">
        <div className="font-medium">
          {n}. {title}
        </div>
        <div className="text-muted-foreground space-y-2 text-sm">{children}</div>
      </div>
    </li>
  )
}

function CopyField({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* sin permiso de portapapeles: el texto queda visible para copiarlo a mano */
    }
  }
  return (
    <div className="flex items-center gap-2">
      <code aria-label={label} className="bg-muted min-w-0 flex-1 truncate rounded px-2 py-1.5 text-xs text-slate-900 dark:text-slate-100">
        {value}
      </code>
      <Button type="button" variant="outline" size="sm" onClick={copy}>
        <Copy className="size-3.5" /> {copied ? 'Copiado' : 'Copiar'}
      </Button>
    </div>
  )
}

function StatusCard({ status }: { status: OrganicStatus }) {
  const ok = status.connected && !status.needs_reauthorization
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="text-base">Estado de la conexión</CardTitle>
        <div className="flex flex-wrap gap-2">
          <Pill label={status.configured ? 'Claves configuradas' : 'Faltan claves'} className={status.configured ? PILL.green : PILL.red} />
          <Pill label={ok ? 'Conectado' : status.needs_reauthorization ? 'Hay que volver a autorizar' : 'No conectado'} className={ok ? PILL.green : status.needs_reauthorization ? PILL.amber : PILL.slate} />
        </div>
      </CardHeader>
      <CardContent className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
        <div>
          <span className="text-muted-foreground">Cuenta: </span>
          {status.username ? `@${status.username}` : '—'}
        </div>
        <div>
          <span className="text-muted-foreground">Último dato guardado: </span>
          {status.last_snapshot_date ?? '—'}
        </div>
        <div>
          <span className="text-muted-foreground">La autorización vence: </span>
          {dateTime(status.refresh_expires_at)}
          {status.refresh_days_left != null && <span className="text-muted-foreground"> ({status.refresh_days_left} días)</span>}
        </div>
        <div>
          <span className="text-muted-foreground">Permisos concedidos: </span>
          {status.scopes.length ? status.scopes.join(', ') : '—'}
        </div>
        {status.missing_scopes.length > 0 && (
          <div className="sm:col-span-2">
            <Callout tone="warning">
              Falta que actives estos permisos en el portal de TikTok y vuelvas a autorizar: <strong>{status.missing_scopes.join(', ')}</strong>. Sin ellos algunas pantallas quedarán incompletas.
            </Callout>
          </div>
        )}
        {status.token_file_error && (
          <div className="sm:col-span-2">
            <Callout tone="danger">No se pudo leer la autorización guardada: {status.token_file_error}</Callout>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * Conexión de la cuenta orgánica de TikTok. Guía los pasos únicos (registrar la URL de retorno, activar permisos,
 * autorizar y pegar la URL a la que TikTok redirige) y deja la cuenta lista para sincronizar. Solo lectura: la app
 * nunca publica ni edita contenido, y los tokens se quedan en el servidor.
 */
export function TikTokOrganicConnection() {
  const [reload, setReload] = useState(0)
  const status = useAsync(() => tiktokOrganic.status(), [reload])
  const [auth, setAuth] = useState<OrganicAuthorizeUrl | null>(null)
  const [pasted, setPasted] = useState('')
  const [busy, setBusy] = useState<'auth' | 'connect' | 'disconnect' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const run = async (kind: 'auth' | 'connect' | 'disconnect', task: () => Promise<void>) => {
    setBusy(kind)
    setError(null)
    setNotice(null)
    try {
      await task()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const authorize = () =>
    run('auth', async () => {
      const next = await tiktokOrganic.authorizeUrl()
      setAuth(next)
      window.open(next.authorize_url, '_blank', 'noopener,noreferrer')
      setNotice('Se abrió TikTok en otra pestaña. Autoriza la cuenta y vuelve aquí con la URL a la que te redirija.')
    })

  const connect = () =>
    run('connect', async () => {
      const parsed = parseCallback(pasted)
      if (!parsed) throw new Error('No encontré el código en lo que pegaste. Pega la URL completa a la que TikTok te redirigió.')
      if (parsed.error) throw new Error(`TikTok no autorizó la cuenta: ${parsed.error}`)
      const state = parsed.state ?? auth?.state
      if (!parsed.code || !state) throw new Error('Falta el código o el estado. Genera la autorización de nuevo y pega la URL completa.')
      await tiktokOrganic.connect(parsed.code, state)
      setPasted('')
      setAuth(null)
      setReload((n) => n + 1)
      setNotice('Cuenta conectada. La sincronización automática empezará a guardar tus videos; puedes forzarla desde "Sincronizar ahora".')
    })

  const disconnect = () => {
    if (!window.confirm('¿Desconectar la cuenta? Se borra la autorización guardada en el servidor (no se pierde lo ya sincronizado).')) return
    void run('disconnect', async () => {
      await tiktokOrganic.disconnect()
      setAuth(null)
      setReload((n) => n + 1)
      setNotice('Cuenta desconectada.')
    })
  }

  const s = status.data
  const connected = !!s?.connected && !s.needs_reauthorization

  return (
    <div className="space-y-4">
      <ErrorAlert message={status.error} />
      {s && <StatusCard status={s} />}

      {s && !connected && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Cómo conectar tu cuenta (solo se hace una vez)</CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="space-y-5">
              <Step n={1} done={!!s.configured && !!s.redirect_uri} title="Prepara las claves y la URL de retorno">
                <p>
                  En <code>backend/.env</code> deben estar <code>TIKTOK_ORGANIC_CLIENT_KEY</code>, <code>TIKTOK_ORGANIC_CLIENT_SECRET</code> y <code>TIKTOK_ORGANIC_REDIRECT_URI</code>. Reinicia el backend después de cambiarlo.
                </p>
                {s.redirect_uri ? (
                  <>
                    <p>Esta misma URL debe estar registrada, exacta, en el portal de TikTok (Login Kit → Redirect URI):</p>
                    <CopyField value={s.redirect_uri} label="URL de retorno" />
                  </>
                ) : (
                  <Callout tone="warning">Todavía no hay <code>TIKTOK_ORGANIC_REDIRECT_URI</code> en el servidor.</Callout>
                )}
                {s.pending_steps.length > 0 && (
                  <ul className="list-disc space-y-1 pl-5">
                    {s.pending_steps.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                )}
              </Step>

              <Step n={2} title="Activa los permisos en la app de TikTok for Developers">
                <ul className="space-y-1">
                  {REQUIRED_SCOPES.map((r) => (
                    <li key={r.scope}>
                      <code>{r.scope}</code> — {r.why}
                    </li>
                  ))}
                </ul>
                <p>Si la app está en modo prueba, agrega tu cuenta como usuario de prueba.</p>
              </Step>

              <Step n={3} done={!!auth} title="Autoriza la cuenta en TikTok">
                <Button type="button" onClick={authorize} disabled={busy !== null || !s.configured || !s.redirect_uri}>
                  <ExternalLink className="size-4" /> {busy === 'auth' ? 'Abriendo…' : 'Abrir autorización de TikTok'}
                </Button>
                {auth && <p>El enlace es válido {Math.round(auth.expires_in_seconds / 60)} minutos. Si caduca, genéralo de nuevo.</p>}
              </Step>

              <Step n={4} title="Pega aquí la URL a la que TikTok te redirigió">
                <p>
                  Al terminar, la barra de direcciones tendrá algo como <code>…?code=…&amp;state=…</code>. Copia la URL completa y pégala.
                  <strong> No compartas ese código con nadie, ni lo pegues en un chat:</strong> da acceso a tu cuenta.
                </p>
                <textarea
                  value={pasted}
                  onChange={(e) => setPasted(e.target.value)}
                  rows={3}
                  spellCheck={false}
                  autoComplete="off"
                  aria-label="URL de retorno de TikTok"
                  placeholder="https://tu-url-de-retorno/?code=…&state=…"
                  className="bg-background w-full rounded-md border p-2 font-mono text-xs text-slate-900 dark:text-slate-100"
                />
                <Button type="button" onClick={connect} disabled={busy !== null || !pasted.trim()}>
                  {busy === 'connect' ? 'Conectando…' : 'Conectar cuenta'}
                </Button>
              </Step>
            </ol>
          </CardContent>
        </Card>
      )}

      {s && connected && (
        <Callout tone="info">
          La cuenta está conectada. Los datos se guardan una vez al día (el historial empieza desde la conexión, porque TikTok solo entrega las cifras actuales). Mira las otras secciones de TikTok orgánico para ver el rendimiento.
        </Callout>
      )}

      {notice && <Callout tone="info">{notice}</Callout>}
      {error && <Callout tone="danger">{error}</Callout>}

      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" onClick={() => setReload((n) => n + 1)} disabled={status.loading}>
          <RefreshCw className="size-3.5" /> Actualizar estado
        </Button>
        {s?.connected && (
          <Button type="button" variant="outline" size="sm" onClick={disconnect} disabled={busy !== null}>
            <Link2Off className="size-3.5" /> Desconectar cuenta
          </Button>
        )}
      </div>
    </div>
  )
}
