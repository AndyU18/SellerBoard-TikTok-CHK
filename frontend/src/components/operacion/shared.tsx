import type { ReactNode } from 'react'
import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { ErrorAlert } from '@/components/analytics/ui'
import { axisNumber, EmptyChart, PALETTE, TipBox } from '@/components/charts/kit'
import type { AsyncState } from '@/lib/useAsync'

/** Días del período global, para las APIs que reciben `days` (entre 7 y 365). */
export function rangeDays(range: { start?: string; end?: string }): number {
  if (!range.start || !range.end) return 365
  const d = Math.round((Date.parse(range.end) - Date.parse(range.start)) / 86_400_000) + 1
  return Math.max(7, Math.min(365, d))
}

/** "2026-11" -> "nov 26"; "2026-10-05" (lunes de la semana) -> "5 oct". */
export function periodLabel(period: string, granularity: 'week' | 'month'): string {
  if (granularity === 'week') {
    return new Date(`${period}T00:00:00`).toLocaleDateString('es', { day: 'numeric', month: 'short' })
  }
  return new Date(`${period}-01T00:00:00`).toLocaleDateString('es', { month: 'short', year: '2-digit' })
}

/** Muestra error, carga o vacío; si hay datos, el contenido. */
export function AsyncBody<T>({ state, empty, children }: { state: AsyncState<T>; empty?: boolean; children: ReactNode }) {
  if (state.error) return <ErrorAlert message={state.error} />
  if (state.loading && !state.data) return <EmptyChart>Cargando…</EmptyChart>
  if (empty) return <EmptyChart />
  return <>{children}</>
}

export type HBarItem = { id: string; label: string; value: number; color?: string; extra?: string }

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)

/** Barras horizontales (una por fila) con color por barra, tooltip y clic para profundizar. */
export function HBars({
  data,
  format,
  valueLabel = 'Valor',
  onBarClick,
  selected,
  legend,
  axisFormat = axisNumber,
}: {
  data: HBarItem[]
  format: (v: number) => string
  valueLabel?: string
  onBarClick?: (item: HBarItem) => void
  selected?: string | null
  legend?: { color: string; label: string }[]
  axisFormat?: (v: number) => string
}) {
  if (!data.length) return <EmptyChart />
  return (
    <div>
      {legend && (
        <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {legend.map((l) => (
            <span key={l.label} className="flex items-center gap-1.5">
              <span className="inline-block size-3 rounded-sm" style={{ background: l.color }} />
              {l.label}
            </span>
          ))}
        </div>
      )}
      <ResponsiveContainer width="100%" height={Math.max(120, data.length * 28 + 30)}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tickFormatter={axisFormat} tick={{ fontSize: 11 }} />
          <YAxis type="category" dataKey="label" width={130} interval={0} tick={{ fontSize: 11 }} tickFormatter={(v: string) => clip(v, 20)} />
          <Tooltip
            cursor={{ fill: 'rgba(148,163,184,0.15)' }}
            content={({ active, payload }) => {
              const p = active ? (payload?.[0]?.payload as HBarItem | undefined) : undefined
              if (!p) return null
              const rows = [{ label: valueLabel, value: format(p.value), color: p.color ?? PALETTE[0] }]
              if (p.extra) rows.push({ label: 'Detalle', value: p.extra, color: '' })
              return <TipBox title={p.label} rows={rows} />
            }}
          />
          <Bar
            dataKey="value"
            radius={[0, 3, 3, 0]}
            onClick={(d: unknown) => {
              const p = (d as { payload?: HBarItem }).payload
              if (p) onBarClick?.(p)
            }}
          >
            {data.map((d) => (
              <Cell
                key={d.id}
                fill={d.color ?? PALETTE[0]}
                opacity={selected && selected !== d.id ? 0.45 : 1}
                cursor={onBarClick ? 'pointer' : undefined}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

export type ColorSlice = { id: string; label: string; value: number; color: string }

/** Dona con un color fijo por porción (para estados: verde = bien, rojo = problema). Clic filtra. */
export function ColorDonut({
  slices,
  format,
  centerLabel,
  onSliceClick,
  selected,
  height = 220,
}: {
  slices: ColorSlice[]
  format: (v: number) => string
  centerLabel?: string
  onSliceClick?: (s: ColorSlice) => void
  selected?: string | null
  height?: number
}) {
  const [active, setActive] = useState<string | null>(null)
  const data = slices.filter((s) => s.value > 0)
  const total = data.reduce((a, s) => a + s.value, 0)
  if (!data.length) return <EmptyChart />
  const dim = (id: string) => ((active && active !== id) || (selected && selected !== id) ? 0.35 : 1)
  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row">
      <div className="relative w-full sm:w-1/2" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="label" innerRadius="58%" outerRadius="90%" paddingAngle={1} onMouseLeave={() => setActive(null)}>
              {data.map((s) => (
                <Cell
                  key={s.id}
                  fill={s.color}
                  opacity={dim(s.id)}
                  cursor={onSliceClick ? 'pointer' : undefined}
                  onMouseEnter={() => setActive(s.id)}
                  onClick={() => onSliceClick?.(s)}
                />
              ))}
            </Pie>
            <Tooltip
              content={({ active: on, payload }) => {
                const p = on ? (payload?.[0]?.payload as ColorSlice | undefined) : undefined
                return p ? (
                  <TipBox
                    title={p.label}
                    rows={[
                      { label: 'Valor', value: format(p.value), color: p.color },
                      { label: 'Parte', value: `${((p.value / total) * 100).toFixed(1)}%` },
                    ]}
                  />
                ) : null
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
        {data.map((s) => (
          <li
            key={s.id}
            onMouseEnter={() => setActive(s.id)}
            onMouseLeave={() => setActive(null)}
            onClick={() => onSliceClick?.(s)}
            className={`flex items-center justify-between gap-2 rounded px-1 ${onSliceClick ? 'cursor-pointer' : ''} ${active === s.id ? 'bg-slate-100' : ''}`}
          >
            <span className="flex items-center gap-2">
              <span className="inline-block size-2.5 rounded-sm" style={{ background: s.color }} />
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

export const SEMAFORO = { red: '#ef4444', amber: '#f59e0b', green: '#10b981' }
