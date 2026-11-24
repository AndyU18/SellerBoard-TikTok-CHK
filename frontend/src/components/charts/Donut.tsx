import { useState } from 'react'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'

import { EmptyChart, PALETTE, TipBox } from './kit'

export type DonutSlice = { id: string; label: string; value: number }

/** Dona con lista al lado: pasar el cursor resalta la porción, un clic avisa con `onSliceClick`. */
export function Donut({
  slices,
  format,
  height = 240,
  centerLabel,
  onSliceClick,
}: {
  slices: DonutSlice[]
  format: (v: number) => string
  height?: number
  centerLabel?: string
  onSliceClick?: (slice: DonutSlice) => void
}) {
  const [active, setActive] = useState<string | null>(null)
  const data = slices.filter((s) => s.value > 0)
  const total = data.reduce((a, s) => a + s.value, 0)
  if (!data.length) return <EmptyChart />
  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row">
      <div className="relative w-full sm:w-1/2" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="label" innerRadius="58%" outerRadius="90%" paddingAngle={1} onMouseLeave={() => setActive(null)}>
              {data.map((s, i) => (
                <Cell
                  key={s.id}
                  fill={PALETTE[i % PALETTE.length]}
                  opacity={active && active !== s.id ? 0.35 : 1}
                  cursor={onSliceClick ? 'pointer' : undefined}
                  onMouseEnter={() => setActive(s.id)}
                  onClick={() => onSliceClick?.(s)}
                />
              ))}
            </Pie>
            <Tooltip
              content={({ active: on, payload }) => {
                const p = on ? (payload?.[0]?.payload as DonutSlice | undefined) : undefined
                return p ? <TipBox title={p.label} rows={[{ label: 'Monto', value: format(p.value) }, { label: 'Parte', value: `${((p.value / total) * 100).toFixed(1)}%` }]} /> : null
              }}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <div className="text-lg font-semibold">{format(total)}</div>
          {centerLabel && <div className="text-muted-foreground text-xs">{centerLabel}</div>}
        </div>
      </div>
      <ul className="w-full space-y-1 text-sm sm:w-1/2">
        {data.map((s, i) => (
          <li
            key={s.id}
            onMouseEnter={() => setActive(s.id)}
            onMouseLeave={() => setActive(null)}
            onClick={() => onSliceClick?.(s)}
            className={`flex items-center justify-between gap-2 rounded px-1 ${onSliceClick ? 'cursor-pointer' : ''} ${active === s.id ? 'bg-slate-100' : ''}`}
          >
            <span className="flex items-center gap-2">
              <span className="inline-block size-2.5 rounded-sm" style={{ background: PALETTE[i % PALETTE.length] }} />
              {s.label}
            </span>
            <span className="tabular-nums">
              {format(s.value)} <span className="text-muted-foreground text-xs">({((s.value / total) * 100).toFixed(0)}%)</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
