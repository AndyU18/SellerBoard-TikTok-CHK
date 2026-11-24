import { Download, Image as ImageIcon } from 'lucide-react'
import { useRef, type ReactNode } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

/** Paleta común: todas las gráficas usan los mismos colores para que se lean igual. */
export const PALETTE = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899', '#64748b']
export const COLOR = { good: '#10b981', bad: '#ef4444', neutral: '#94a3b8', blue: '#3b82f6', amber: '#f59e0b' }

const compact = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 })
const compactNum = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })
/** Dinero corto para ejes: $12.3K. */
export const axisMoney = (v: number) => compact.format(v)
export const axisNumber = (v: number) => compactNum.format(v)
export const axisPct = (v: number) => `${(v * 100).toFixed(0)}%`

export type CsvRow = Record<string, string | number | null | undefined>

function toCsv(rows: CsvRow[]): string {
  if (!rows.length) return ''
  const cols = Object.keys(rows[0])
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  return [cols.map(esc).join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n')
}

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/**
 * Marco común de las gráficas: título, subtítulo, descarga como imagen (PNG) y como CSV.
 * `csv` son las filas que se bajan; sin él solo hay descarga de imagen.
 */
export function ChartCard({
  title,
  subtitle,
  csv,
  filename,
  actions,
  children,
  className = '',
}: {
  title: string
  subtitle?: string
  csv?: CsvRow[]
  filename?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  const body = useRef<HTMLDivElement>(null)
  const base = (filename ?? title).toLowerCase().replace(/[^a-z0-9]+/g, '-')

  const png = () => {
    const svg = body.current?.querySelector('svg.recharts-surface') ?? body.current?.querySelector('svg')
    if (!svg) return
    const { width, height } = svg.getBoundingClientRect()
    const clone = svg.cloneNode(true) as SVGSVGElement
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
    clone.setAttribute('width', String(width))
    clone.setAttribute('height', String(height))
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml' }))
    const img = new Image()
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = width * 2
      canvas.height = height * 2
      const ctx = canvas.getContext('2d')
      if (ctx) {
        ctx.fillStyle = '#fff'
        ctx.fillRect(0, 0, canvas.width, canvas.height)
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
        canvas.toBlob((b) => b && save(b, `${base}.png`))
      }
      URL.revokeObjectURL(url)
    }
    img.src = url
  }

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div>
          <CardTitle className="text-base">{title}</CardTitle>
          {subtitle && <p className="text-muted-foreground mt-1 text-xs">{subtitle}</p>}
        </div>
        <div className="flex items-center gap-1">
          {actions}
          <button type="button" onClick={png} title="Descargar imagen" aria-label="Descargar imagen" className="text-muted-foreground hover:text-foreground rounded p-1.5 hover:bg-slate-100">
            <ImageIcon className="size-4" />
          </button>
          {csv && csv.length > 0 && (
            <button
              type="button"
              onClick={() => save(new Blob(['﻿' + toCsv(csv)], { type: 'text/csv;charset=utf-8' }), `${base}.csv`)}
              title="Descargar datos (CSV)"
              aria-label="Descargar datos CSV"
              className="text-muted-foreground hover:text-foreground rounded p-1.5 hover:bg-slate-100"
            >
              <Download className="size-4" />
            </button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <div ref={body}>{children}</div>
      </CardContent>
    </Card>
  )
}

/** Mensaje cuando no hay datos que graficar. */
export function EmptyChart({ children = 'Sin datos en este período.' }: { children?: ReactNode }) {
  return <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">{children}</div>
}

/** Cuadro flotante de las gráficas (tooltip) con el mismo estilo en todas. */
export function TipBox({ title, rows }: { title?: ReactNode; rows: { label: ReactNode; value: ReactNode; color?: string }[] }) {
  return (
    <div className="rounded-md border bg-white p-2 text-xs shadow-md dark:bg-slate-900">
      {title && <div className="mb-1 font-medium">{title}</div>}
      {rows.map((r, i) => (
        <div key={i} className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-1.5">
            {r.color && <span className="inline-block size-2 rounded-full" style={{ background: r.color }} />}
            {r.label}
          </span>
          <span className="font-medium tabular-nums">{r.value}</span>
        </div>
      ))}
    </div>
  )
}

/** Escala verde (alto) ↔ rojo (bajo) para mapas de calor. `t` va de 0 a 1. */
export function heatColor(t: number): string {
  const hue = 8 + Math.max(0, Math.min(1, t)) * 117 // 8 = rojo, 125 = verde
  return `hsl(${hue} 62% ${88 - Math.abs(t - 0.5) * 36}%)`
}

/** Escala de un solo color (azul) para intensidades: 0 = claro, 1 = oscuro. */
export function blueScale(t: number): string {
  const x = Math.max(0, Math.min(1, t))
  return `hsl(217 ${40 + x * 40}% ${96 - x * 52}%)`
}
