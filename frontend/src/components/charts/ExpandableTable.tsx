import { ChevronDown, ChevronRight } from 'lucide-react'
import { useState } from 'react'

export type TreeRow = {
  id: string
  label: string
  /** Un texto ya formateado por columna (mismo orden que `columns`). */
  cells: string[]
  /** Valores numéricos para colorear (rojo si es negativo). */
  negative?: boolean[]
  bold?: boolean
  /** Fila de indicador (fondo gris, como TACOS o Margen). */
  shaded?: boolean
  children?: TreeRow[]
}

/**
 * Tabla expandible estilo estado de resultados: las filas con `children` se abren y cierran
 * (por ejemplo "Ventas" se abre por canal o por SKU).
 */
export function ExpandableTable({ columns, rows, firstColumn = 'Concepto' }: { columns: string[]; rows: TreeRow[]; firstColumn?: string }) {
  const [open, setOpen] = useState<Set<string>>(new Set())
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const render = (r: TreeRow, depth: number): React.ReactNode[] => {
    const expandable = !!r.children?.length
    const isOpen = open.has(r.id)
    const line = (
      <tr key={r.id} className={`border-b ${r.shaded ? 'bg-slate-100 dark:bg-slate-800' : ''} ${r.bold ? 'font-semibold' : ''}`}>
        <td className="py-1.5 pr-2" style={{ paddingLeft: 8 + depth * 18 }}>
          {expandable ? (
            <button type="button" onClick={() => toggle(r.id)} aria-expanded={isOpen} className="flex items-center gap-1 text-left">
              {isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
              {r.label}
            </button>
          ) : (
            <span className="pl-5">{r.label}</span>
          )}
        </td>
        {r.cells.map((c, i) => (
          <td key={i} className={`px-2 py-1.5 text-right tabular-nums ${r.negative?.[i] ? 'text-red-600' : ''}`}>
            {c}
          </td>
        ))}
      </tr>
    )
    return [line, ...(expandable && isOpen ? r.children!.flatMap((c) => render(c, depth + 1)) : [])]
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[28rem] text-sm">
        <thead>
          <tr className="text-muted-foreground border-b text-xs">
            <th className="px-2 py-2 text-left font-medium">{firstColumn}</th>
            {columns.map((c) => (
              <th key={c} className="px-2 py-2 text-right font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{rows.flatMap((r) => render(r, 0))}</tbody>
      </table>
    </div>
  )
}
