// Piezas compartidas por las pantallas de Walmart (calidad, WFS, feeds): tabla ordenable, búsqueda y CSV.
import { Download } from 'lucide-react'

import type { CsvRow } from '@/components/charts/kit'
import { TableHead } from '@/components/ui/table'

import type { SortState } from './walmartSort'

export function SortHead<K extends string>({
  sortKey,
  label,
  sort,
  onSort,
  right = true,
}: {
  sortKey: K
  label: string
  sort: SortState<K>
  onSort: (key: K) => void
  right?: boolean
}) {
  const active = sort.key === sortKey
  return (
    <TableHead className={right ? 'text-right' : ''} aria-sort={active ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button type="button" onClick={() => onSort(sortKey)} className="rounded hover:underline focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
        {label}
        {active ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
      </button>
    </TableHead>
  )
}

export function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <input
      type="search"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      aria-label={placeholder}
      className="h-9 w-full rounded-md border px-3 text-sm sm:w-56"
    />
  )
}

function csvText(rows: CsvRow[]): string {
  const cols = Object.keys(rows[0] ?? {})
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  return [cols.map(esc).join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n')
}

export function CsvButton({ filename, rows }: { filename: string; rows: CsvRow[] }) {
  const save = () => {
    const url = URL.createObjectURL(new Blob(['﻿' + csvText(rows)], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return (
    <button
      type="button"
      onClick={save}
      disabled={rows.length === 0}
      className="text-muted-foreground hover:text-foreground flex items-center gap-1 rounded border px-2 py-1.5 text-xs hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none disabled:opacity-40"
    >
      <Download className="size-4" aria-hidden /> CSV
    </button>
  )
}

/** Encabezado estándar de página: título con el alcance y una línea de contexto opcional. */
export function PageTitle({ title, scope, note }: { title: string; scope: string; note?: string }) {
  return (
    <div>
      <h1 className="text-2xl font-semibold">
        {title}
        {scope ? ` · ${scope}` : ''}
      </h1>
      {note && <p className="text-muted-foreground text-xs">{note}</p>}
    </div>
  )
}

// Compatibilidad: otras pantallas de Walmart importan el orden desde aquí.
export { compareValues, nextSort } from './walmartSort'
export type { SortDir, SortState } from './walmartSort'
