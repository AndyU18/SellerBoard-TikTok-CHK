// Piezas visuales compartidas por las pantallas de Seguridad.
import { ExternalLink } from 'lucide-react'
import type { ReactNode } from 'react'

import { Callout, Pill } from '@/components/analytics/ui'

import { gitBadge, NO_EDIT_PERMISSION_HINT, type Badge } from './securityFormat'

/** Título de cada pantalla con una línea que explica qué se está viendo. */
export function SecurityHeader({ title, note, children }: { title: string; note?: string; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-xl font-semibold">{title}</h2>
        {note && <p className="text-muted-foreground mt-1 max-w-3xl text-sm">{note}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  )
}

export function LoadingNote() {
  return <div className="text-muted-foreground text-sm">Cargando…</div>
}

export function BadgePill({ badge }: { badge: Badge }) {
  return <Pill label={badge.label} className={badge.className} />
}

export function GitPill({ ignored }: { ignored: boolean | null }) {
  return <BadgePill badge={gitBadge(ignored)} />
}

/** Aviso fijo para quien no puede editar: explica por qué los botones están desactivados. */
export function ReadOnlyNotice() {
  return <Callout tone="info">{NO_EDIT_PERMISSION_HINT}</Callout>
}

/** Enlace externo al portal de una plataforma (sin pasar referencia ni acceso a la ventana de origen). */
export function PortalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-sm font-medium text-blue-700 underline underline-offset-2 hover:text-blue-900 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none dark:text-blue-400"
    >
      {children}
      <ExternalLink className="size-3.5" aria-hidden="true" />
      <span className="sr-only">(se abre en otra pestaña)</span>
    </a>
  )
}
