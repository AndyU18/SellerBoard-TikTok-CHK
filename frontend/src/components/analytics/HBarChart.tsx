import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { EmptyChart, PALETTE, TipBox } from '@/components/charts/kit'

export type HBarRow = { id: string; label: string; [key: string]: string | number | null | undefined }
export type HBarSeries = { key: string; label: string; color?: string; stack?: string }

/**
 * Barras horizontales (ranking). Leyenda que enciende/apaga series, tooltip y clic en una barra.
 * `rowColor` permite pintar cada barra según su valor (por ejemplo rojo si es negativo).
 */
export function HBarChart({
  rows,
  series,
  format,
  refLine,
  rowColor,
  selectedId,
  onRowClick,
  labelWidth = 140,
  rowHeight = 28,
}: {
  rows: HBarRow[]
  series: HBarSeries[]
  format: (v: number) => string
  refLine?: number
  rowColor?: (row: HBarRow, key: string) => string | undefined
  selectedId?: string | null
  onRowClick?: (row: HBarRow) => void
  labelWidth?: number
  rowHeight?: number
}) {
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  if (!rows.length) return <EmptyChart />
  const color = (s: HBarSeries, i: number) => s.color ?? PALETTE[i % PALETTE.length]
  const toggle = (k: string) =>
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  const height = Math.max(160, rows.length * rowHeight * Math.max(1, series.filter((s) => !s.stack).length) + 40)
  return (
    <div>
      {series.length > 1 && (
        <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {series.map((s, i) => (
            <button key={s.key} type="button" aria-pressed={!hidden.has(s.key)} onClick={() => toggle(s.key)} className={`flex items-center gap-1.5 ${hidden.has(s.key) ? 'opacity-40' : ''}`}>
              <span className="inline-block size-3 rounded-sm" style={{ background: color(s, i) }} />
              {s.label}
            </button>
          ))}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tickFormatter={format} tick={{ fontSize: 11 }} />
          <YAxis type="category" dataKey="label" width={labelWidth} tick={{ fontSize: 11 }} interval={0} />
          {refLine != null && <ReferenceLine x={refLine} stroke="#64748b" strokeDasharray="4 4" />}
          <Tooltip
            cursor={{ fill: 'rgba(148,163,184,0.15)' }}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <TipBox
                  title={String(label)}
                  rows={payload.map((p) => ({
                    label: series.find((s) => s.key === p.dataKey)?.label ?? String(p.name),
                    value: format(Number(p.value)),
                    color: String(p.color ?? ''),
                  }))}
                />
              ) : null
            }
          />
          {series
            .filter((s) => !hidden.has(s.key))
            .map((s) => {
              const i = series.indexOf(s)
              return (
                <Bar
                  key={s.key}
                  dataKey={s.key}
                  name={s.label}
                  stackId={s.stack}
                  radius={[0, 3, 3, 0]}
                  fill={color(s, i)}
                  cursor={onRowClick ? 'pointer' : undefined}
                  onClick={(d: unknown) => {
                    const row = (d as { payload?: HBarRow }).payload
                    if (row && onRowClick) onRowClick(row)
                  }}
                >
                  {rows.map((r) => (
                    <Cell key={r.id} fill={rowColor?.(r, s.key) ?? color(s, i)} fillOpacity={selectedId && selectedId !== r.id ? 0.45 : 1} />
                  ))}
                </Bar>
              )
            })}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
