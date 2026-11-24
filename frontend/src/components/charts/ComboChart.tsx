import { useState } from 'react'
import { Area, Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { axisMoney, EmptyChart, PALETTE, TipBox } from './kit'

export type ComboSeries = {
  key: string
  label: string
  type: 'bar' | 'line' | 'area'
  /** Eje donde se dibuja; el derecho sirve para series de otra escala (unidades, porcentaje...). */
  axis?: 'left' | 'right'
  color?: string
  format?: (v: number) => string
  /** Series con el mismo `stack` se apilan. */
  stack?: string
}

type Row = Record<string, string | number | null | undefined>

/**
 * Barras + líneas con doble eje. La leyenda enciende y apaga cada serie; al pasar el cursor se ve el
 * detalle de todas; un clic en una barra o punto avisa con `onPointClick` (para profundizar).
 */
export function ComboChart({
  data,
  xKey,
  series,
  height = 320,
  xFormat,
  leftFormat = axisMoney,
  rightFormat,
  onPointClick,
}: {
  data: Row[]
  xKey: string
  series: ComboSeries[]
  height?: number
  xFormat?: (v: string | number) => string
  leftFormat?: (v: number) => string
  rightFormat?: (v: number) => string
  onPointClick?: (row: Row) => void
}) {
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  if (!data.length) return <EmptyChart />
  const color = (s: ComboSeries, i: number) => s.color ?? PALETTE[i % PALETTE.length]
  const visible = series.filter((s) => !hidden.has(s.key))
  const hasRight = visible.some((s) => s.axis === 'right')
  const toggle = (k: string) =>
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  const click = (payload: unknown) => {
    const row = (payload as { payload?: Row } | undefined)?.payload
    if (row && onPointClick) onPointClick(row)
  }

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {series.map((s, i) => (
          <button
            key={s.key}
            type="button"
            aria-pressed={!hidden.has(s.key)}
            onClick={() => toggle(s.key)}
            className={`flex items-center gap-1.5 ${hidden.has(s.key) ? 'opacity-40' : ''}`}
          >
            <span
              className={`inline-block size-3 ${s.type === 'bar' ? 'rounded-sm' : 'rounded-full border-2 bg-white'}`}
              style={s.type === 'bar' ? { background: color(s, i) } : { borderColor: color(s, i) }}
            />
            {s.label}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey={xKey} tickFormatter={xFormat} tick={{ fontSize: 11 }} />
          <YAxis yAxisId="left" tickFormatter={leftFormat} tick={{ fontSize: 11 }} width={56} />
          {hasRight && (
            <YAxis yAxisId="right" orientation="right" tickFormatter={rightFormat} tick={{ fontSize: 11 }} width={48} />
          )}
          <Tooltip
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <TipBox
                  title={xFormat ? xFormat(label as string) : String(label)}
                  rows={payload.map((p) => {
                    const s = series.find((x) => x.key === p.dataKey)
                    const v = Number(p.value)
                    return { label: s?.label ?? String(p.name), value: s?.format ? s.format(v) : leftFormat(v), color: String(p.color ?? p.stroke ?? '') }
                  })}
                />
              ) : null
            }
          />
          {visible.map((s) => {
            const i = series.indexOf(s)
            const common = { yAxisId: s.axis ?? 'left', dataKey: s.key, name: s.label }
            if (s.type === 'bar')
              return <Bar key={s.key} {...common} fill={color(s, i)} stackId={s.stack} radius={[3, 3, 0, 0]} cursor={onPointClick ? 'pointer' : undefined} onClick={click} />
            if (s.type === 'area')
              return <Area key={s.key} {...common} type="monotone" stroke={color(s, i)} fill={color(s, i)} fillOpacity={0.15} stackId={s.stack} />
            return (
              <Line
                key={s.key}
                {...common}
                type="monotone"
                stroke={color(s, i)}
                strokeWidth={2}
                dot={{ r: 3, fill: '#fff', strokeWidth: 2 }}
                activeDot={{ r: 6, onClick: (_e: unknown, p: unknown) => click(p) }}
              />
            )
          })}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
