import { useState } from 'react'

import { blueScale } from './kit'

/**
 * Mapa de calor de una matriz (por ejemplo día de la semana × hora). `values[fila][columna]`.
 * Al pasar el cursor se muestra el detalle de la celda; un clic avisa con `onCellClick`.
 */
export function HeatGrid({
  rowLabels,
  colLabels,
  values,
  format,
  unit = '',
  onCellClick,
}: {
  rowLabels: string[]
  colLabels: string[]
  values: number[][]
  format: (v: number) => string
  unit?: string
  onCellClick?: (row: number, col: number) => void
}) {
  const [hover, setHover] = useState<[number, number] | null>(null)
  const flat = values.flat()
  const max = Math.max(0, ...flat)
  if (!flat.length) return <div className="text-muted-foreground p-4 text-sm">Sin datos para mostrar.</div>
  return (
    <div>
      <div className="overflow-x-auto">
        <div className="grid min-w-[36rem] gap-0.5" style={{ gridTemplateColumns: `4.5rem repeat(${colLabels.length}, minmax(0, 1fr))` }}>
          <div />
          {colLabels.map((c) => (
            <div key={c} className="text-muted-foreground text-center text-[10px]">
              {c}
            </div>
          ))}
          {rowLabels.map((r, ri) => (
            <div key={r} className="contents">
              <div className="text-muted-foreground pr-2 text-right text-xs leading-6">{r}</div>
              {colLabels.map((c, ci) => {
                const v = values[ri]?.[ci] ?? 0
                return (
                  <button
                    key={c}
                    type="button"
                    aria-label={`${r} ${c}: ${format(v)}`}
                    onMouseEnter={() => setHover([ri, ci])}
                    onMouseLeave={() => setHover(null)}
                    onClick={() => onCellClick?.(ri, ci)}
                    className={`h-6 rounded-sm ${hover?.[0] === ri && hover[1] === ci ? 'ring-2 ring-slate-700' : ''}`}
                    style={{ background: blueScale(max ? v / max : 0) }}
                  />
                )
              })}
            </div>
          ))}
        </div>
      </div>
      <div className="text-muted-foreground mt-2 h-5 text-xs">
        {hover ? (
          <>
            <strong className="text-foreground">
              {rowLabels[hover[0]]} · {colLabels[hover[1]]}
            </strong>
            : {format(values[hover[0]]?.[hover[1]] ?? 0)} {unit}
          </>
        ) : (
          'Pasa el cursor sobre una celda para ver el detalle.'
        )}
      </div>
    </div>
  )
}
