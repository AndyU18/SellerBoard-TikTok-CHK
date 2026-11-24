import { ArrowDown, ArrowUp } from 'lucide-react'
import { useMemo, useState } from 'react'

import { heatColor } from './kit'
import { Sparkline } from './SparkKpi'

export type HeatRow = {
  id: string
  label: string
  sublabel?: string
  /** Un valor por columna (mismo orden que `columns`). */
  values: number[]
}

/**
 * Tabla con mapa de calor y mini-gráfico por fila (como la de ventas por producto y mes).
 * El color de cada celda va de rojo (lo más bajo de esa fila) a verde (lo más alto); debajo del valor se
 * muestra la variación contra la columna anterior. Clic en el encabezado ordena; clic en una fila avisa.
 */
export function HeatTable({
  columns,
  rows,
  format,
  firstColumn = 'Producto',
  colorBy = 'row',
  showChange = true,
  onRowClick,
  maxRows = 50,
}: {
  columns: string[]
  rows: HeatRow[]
  format: (v: number) => string
  firstColumn?: string
  colorBy?: 'row' | 'column'
  showChange?: boolean
  onRowClick?: (row: HeatRow) => void
  maxRows?: number
}) {
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null)

  const sorted = useMemo(() => {
    if (!sort) return rows
    return [...rows].sort((a, b) => (a.values[sort.col] - b.values[sort.col]) * sort.dir)
  }, [rows, sort])

  const colRange = useMemo(
    () =>
      columns.map((_, c) => {
        const vs = rows.map((r) => r.values[c])
        return [Math.min(...vs), Math.max(...vs)] as const
      }),
    [columns, rows],
  )

  const shade = (row: HeatRow, c: number) => {
    const [lo, hi] = colorBy === 'row' ? [Math.min(...row.values), Math.max(...row.values)] : colRange[c]
    return heatColor(hi === lo ? 0.5 : (row.values[c] - lo) / (hi - lo))
  }

  const toggle = (col: number) => setSort((s) => (s?.col === col ? (s.dir === -1 ? { col, dir: 1 } : null) : { col, dir: -1 }))

  if (!rows.length) return <div className="text-muted-foreground p-4 text-sm">Sin datos para mostrar.</div>
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-separate border-spacing-0.5 text-sm">
        <thead>
          <tr className="text-muted-foreground text-xs">
            <th className="bg-background sticky left-0 z-10 px-2 py-2 text-left font-medium">{firstColumn}</th>
            <th className="px-2 py-2 font-medium">Tendencia</th>
            {columns.map((c, i) => (
              <th key={c} className="px-2 py-2 text-right font-medium">
                <button type="button" onClick={() => toggle(i)} className="inline-flex items-center gap-1 hover:underline">
                  {c}
                  {sort?.col === i && (sort.dir === -1 ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.slice(0, maxRows).map((r) => (
            <tr key={r.id} onClick={() => onRowClick?.(r)} className={onRowClick ? 'cursor-pointer hover:brightness-95' : ''}>
              <td className="bg-background sticky left-0 z-10 max-w-[16rem] px-2 py-1.5">
                <div className="truncate font-medium" title={r.label}>
                  {r.label}
                </div>
                {r.sublabel && <div className="text-muted-foreground truncate text-xs">{r.sublabel}</div>}
              </td>
              <td className="px-2">
                <Sparkline values={r.values} />
              </td>
              {r.values.map((v, c) => {
                const prev = c > 0 ? r.values[c - 1] : null
                const change = showChange && prev ? (v - prev) / Math.abs(prev) : null
                return (
                  <td key={c} className="rounded px-2 py-1.5 text-right tabular-nums" style={{ background: shade(r, c) }} title={`${r.label} · ${columns[c]}`}>
                    <div className="font-medium">{format(v)}</div>
                    {change != null && (
                      <div className={`text-xs ${change >= 0 ? 'text-emerald-700' : 'text-red-700'}`}>
                        {change >= 0 ? '+' : ''}
                        {(change * 100).toFixed(0)}%
                      </div>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > maxRows && <p className="text-muted-foreground mt-1 text-xs">Se muestran {maxRows} de {rows.length} filas.</p>}
    </div>
  )
}
