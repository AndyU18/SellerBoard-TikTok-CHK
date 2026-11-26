// Piezas compartidas por Rendimiento por producto, Rendimiento por SKU y Catálogo de TikTok Shop:
// imagen con marcador de iniciales, panel lateral accesible y utilidades de formato/filtrado.
import { X } from 'lucide-react'
import { type ReactNode, useEffect, useRef, useState } from 'react'

import { initials } from './tiktokCatalogFormat'

/** Imagen de producto con carga diferida; si falta o falla muestra las iniciales. */
export function ProductImage({
  src,
  title,
  className = 'size-12',
}: {
  src: string | null | undefined
  title: string | null | undefined
  className?: string
}) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null)
  if (!src || failedSrc === src) {
    return (
      <span
        role="img"
        aria-label={`Sin imagen: ${title ?? 'producto'}`}
        className={`${className} flex shrink-0 items-center justify-center rounded-md bg-slate-100 text-sm font-semibold text-slate-500`}
      >
        {initials(title)}
      </span>
    )
  }
  return (
    <img
      src={src}
      alt={title ?? 'Producto de TikTok Shop'}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailedSrc(src)}
      className={`${className} shrink-0 rounded-md bg-slate-100 object-cover`}
    />
  )
}

/** Panel lateral derecho: cierra con Escape, con el fondo o con el botón; devuelve el foco al cerrar. */
export function SidePanel({ title, subtitle, onClose, children }: { title: string; subtitle?: string; onClose: () => void; children: ReactNode }) {
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      opener?.focus?.()
    }
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden />
      <aside role="dialog" aria-modal="true" aria-label={title} className="relative h-full w-full max-w-lg overflow-y-auto bg-white p-5 shadow-xl dark:bg-slate-900">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg leading-tight font-semibold wrap-break-word">{title}</h2>
            {subtitle && <p className="text-muted-foreground mt-0.5 text-xs break-all">{subtitle}</p>}
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Cerrar panel"
            className="text-muted-foreground hover:text-foreground rounded p-1.5 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <div className="space-y-4">{children}</div>
      </aside>
    </div>
  )
}

export function Fact({ label, value, className = '' }: { label: string; value: string; className?: string }) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className={`font-medium ${className}`}>{value}</dd>
    </div>
  )
}

