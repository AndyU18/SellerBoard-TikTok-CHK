// Piezas visuales compartidas por las pantallas de Servicio al cliente de TikTok.
import { Check, Copy, ShieldCheck } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import { Callout, Hint, PILL, Pill } from '@/components/analytics/ui'
import { useScopeDescription } from '@/components/PageHeader'
import { count } from '@/lib/format'
import type { CsSyncState } from '@/lib/tiktokService'

import { shortDate } from './tiktokFormat'
import { shortId } from './tiktokServiceFormat'

export const PRIVACY_NOTICE = 'Esta pantalla solo usa cifras: no se guarda el texto de los mensajes ni datos de compradores.'

/** Tope de conversaciones que TikTok permite listar (medido). */
const LISTING_CAP = 1000
const COPIED_RESET_MS = 1500
/** Tolerancia (días) antes de avisar que el historial empieza después del inicio del período. */
const HISTORY_GAP_DAYS = 3
const DAY_MS = 86_400_000

/** Título de la pantalla con el alcance elegido y el aviso de privacidad. */
export function ServiceHeader({ title, note, children }: { title: string; note?: string; children?: ReactNode }) {
  const description = useScopeDescription()
  return (
    <header className="space-y-1">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="text-muted-foreground text-sm">{description}</p>
      {note && <Hint>{note}</Hint>}
      <p className="text-muted-foreground flex max-w-3xl items-center gap-1.5 text-xs">
        <ShieldCheck className="size-3.5 shrink-0" aria-hidden />
        {PRIVACY_NOTICE}
      </p>
      {children}
    </header>
  )
}

/** Marca las métricas que calcula TikTok (no la app), para no mezclarlas con las propias. */
export function OfficialBadge() {
  return <Pill label="Dato oficial de TikTok" className={PILL.blue} />
}

/** Id corto de conversación que copia el id completo (opaco: no identifica a ninguna persona). */
export function CopyId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard
      ?.writeText(id)
      .then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), COPIED_RESET_MS)
      })
      .catch(() => undefined)
  }
  return (
    <button
      type="button"
      onClick={copy}
      title="Copiar id de la conversación"
      aria-label={`Copiar id de la conversación ${shortId(id)}`}
      className="flex items-center gap-1 rounded font-mono text-xs hover:underline focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none"
    >
      {shortId(id)}
      {copied ? <Check className="size-3 text-green-700" aria-hidden /> : <Copy className="text-muted-foreground size-3" aria-hidden />}
      {copied && <span className="sr-only">Copiado</span>}
    </button>
  )
}

/** Avisos de cobertura que trae el backend: carga inicial incompleta, tope de TikTok e historial corto. */
export function CoverageNotices({ sync, windowDays, firstDay, generatedAt }: { sync: CsSyncState; windowDays: number; firstDay: string | null; generatedAt: string }) {
  const notices: { key: string; text: string }[] = []
  if (sync.initial_load_complete === false) {
    const missing = sync.pending_remaining ? ` Faltan ${count(sync.pending_remaining)} conversaciones por revisar.` : ''
    notices.push({ key: 'load', text: `La carga inicial de conversaciones sigue en curso: las cifras pueden estar incompletas.${missing}` })
  }
  if ((sync.conversations_listed ?? 0) >= LISTING_CAP) {
    notices.push({
      key: 'cap',
      text: `TikTok solo permite listar unas ${count(LISTING_CAP)} conversaciones (las de más actividad reciente): las más antiguas pueden faltar.`,
    })
  }
  if (firstDay) {
    const gapDays = (new Date(firstDay).getTime() - (new Date(generatedAt).getTime() - windowDays * DAY_MS)) / DAY_MS
    if (gapDays > HISTORY_GAP_DAYS) {
      notices.push({ key: 'history', text: `El historial disponible empieza el ${shortDate(firstDay)}: el período elegido es más largo que los datos.` })
    }
  }
  if (!notices.length) return null
  return (
    <div className="space-y-2">
      {notices.map((n) => (
        <Callout key={n.key} tone="warning">
          {n.text}
        </Callout>
      ))}
    </div>
  )
}
