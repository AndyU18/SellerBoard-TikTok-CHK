import type { ReactNode } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

/** Tarjeta de indicador. */
export function Kpi({
  title,
  value,
  hint,
  className = '',
}: {
  title: string
  value: string
  hint?: string
  className?: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-semibold ${className}`}>{value}</div>
        {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
      </CardContent>
    </Card>
  )
}

export function ErrorAlert({ message }: { message: string | null | undefined }) {
  if (!message) return null
  return (
    <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
      No se pudo cargar: {message}
    </div>
  )
}

export function Callout({ tone = 'warning', children }: { tone?: 'warning' | 'danger' | 'info'; children: ReactNode }) {
  const styles = {
    warning: 'border-amber-300 bg-amber-50 text-amber-900',
    danger: 'border-red-300 bg-red-50 text-red-800',
    info: 'border-slate-300 bg-slate-50 text-slate-800',
  }[tone]
  return (
    <div role={tone === 'danger' ? 'alert' : 'status'} className={`rounded-md border p-3 text-sm ${styles}`}>
      {children}
    </div>
  )
}

/** Etiqueta de estado con color. */
export function Pill({ label, className }: { label: string; className: string }) {
  return <span className={`rounded px-2 py-0.5 text-xs whitespace-nowrap ${className}`}>{label}</span>
}

export const PILL = {
  red: 'bg-red-100 text-red-800',
  amber: 'bg-amber-100 text-amber-800',
  orange: 'bg-orange-100 text-orange-800',
  green: 'bg-green-100 text-green-800',
  blue: 'bg-blue-100 text-blue-800',
  slate: 'bg-slate-100 text-slate-700',
}

/** Botones de opción mutuamente excluyentes (pestañas o filtros). */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: { value: T; label: string; count?: number }[]
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={`rounded-md border px-3 py-1.5 text-sm ${value === o.value ? 'bg-slate-800 text-white' : 'hover:bg-slate-100'}`}
        >
          {o.label}
          {o.count != null && <span className="ml-1 opacity-70">({o.count})</span>}
        </button>
      ))}
    </div>
  )
}

/** Campo numérico que aplica el valor al salir del campo o con Enter (evita una consulta por tecla). */
export function NumberField({
  label,
  value,
  onCommit,
  min,
  max,
  step = 1,
  suffix,
}: {
  label: string
  value: number
  onCommit: (v: number) => void
  min?: number
  max?: number
  step?: number
  suffix?: string
}) {
  const commit = (raw: string) => {
    const n = Number(raw)
    if (raw !== '' && !Number.isNaN(n) && n >= (min ?? -Infinity) && n <= (max ?? Infinity) && n !== value) onCommit(n)
  }
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted-foreground text-xs">{label}</span>
      <span className="flex items-center gap-1">
        <Input
          key={value}
          type="number"
          className="h-9 w-24"
          defaultValue={value}
          min={min}
          max={max}
          step={step}
          onBlur={(e) => commit(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit((e.target as HTMLInputElement).value)
          }}
        />
        {suffix && <span className="text-muted-foreground text-xs">{suffix}</span>}
      </span>
    </label>
  )
}

export function Hint({ children }: { children: ReactNode }) {
  return <p className="text-muted-foreground max-w-3xl text-xs">{children}</p>
}

/** `0.153` -> `15.3%` (el `percent` global muestra 2 decimales; aquí basta 1). */
export const pct1 = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)

export const days1 = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(0)} d`)
