import { useState } from 'react'
import { CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts'

import { EmptyChart, TipBox } from '@/components/charts/kit'

export type BreakEvenPoint = {
  id: string
  label: string
  /** Costo (eje X). */
  x: number
  /** Retorno (eje Y): sobre la diagonal el creador ya cubrió su costo. */
  y: number
  /** Tamaño de la burbuja (pedidos). */
  z: number
  group: string
  extra?: { label: string; value: string }[]
}

/**
 * Dispersión costo vs retorno con diagonal de equilibrio (y = x) y color por grupo. La leyenda enciende y
 * apaga cada grupo; un clic en una burbuja avisa con `onPointClick`.
 */
export function BreakEvenBubble({
  points,
  groups,
  xLabel,
  yLabel,
  zLabel,
  format,
  height = 340,
  onPointClick,
}: {
  points: BreakEvenPoint[]
  groups: Record<string, { label: string; color: string }>
  xLabel: string
  yLabel: string
  zLabel: string
  format: (v: number) => string
  height?: number
  onPointClick?: (p: BreakEvenPoint) => void
}) {
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  if (!points.length) return <EmptyChart />
  const shown = points.filter((p) => !hidden.has(p.group))
  const top = Math.max(1, ...shown.map((p) => Math.max(p.x, p.y)))
  const toggle = (g: string) =>
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(g)) next.delete(g)
      else next.add(g)
      return next
    })

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {Object.entries(groups).map(([g, meta]) => (
          <button key={g} type="button" aria-pressed={!hidden.has(g)} onClick={() => toggle(g)} className={`flex items-center gap-1.5 ${hidden.has(g) ? 'opacity-40' : ''}`}>
            <span className="inline-block size-2.5 rounded-full" style={{ background: meta.color }} />
            {meta.label}
          </button>
        ))}
        <span className="text-muted-foreground flex items-center gap-1.5">
          <span className="inline-block h-0 w-5 border-t-2 border-dashed border-slate-400" />
          Equilibrio (retorno = costo)
        </span>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis type="number" dataKey="x" name={xLabel} tickFormatter={format} tick={{ fontSize: 11 }} domain={[0, 'auto']} label={{ value: xLabel, position: 'insideBottom', offset: -4, fontSize: 11 }} />
          <YAxis type="number" dataKey="y" name={yLabel} tickFormatter={format} tick={{ fontSize: 11 }} width={60} domain={[0, 'auto']} label={{ value: yLabel, angle: -90, position: 'insideLeft', fontSize: 11 }} />
          <ZAxis type="number" dataKey="z" range={[60, 500]} name={zLabel} />
          <ReferenceLine segment={[{ x: 0, y: 0 }, { x: top, y: top }]} stroke="#94a3b8" strokeDasharray="5 5" ifOverflow="extendDomain" />
          <Tooltip
            cursor={{ strokeDasharray: '3 3' }}
            content={({ active, payload }) => {
              const p = active ? (payload?.[0]?.payload as BreakEvenPoint | undefined) : undefined
              if (!p) return null
              return (
                <TipBox
                  title={p.label}
                  rows={[
                    { label: xLabel, value: format(p.x) },
                    { label: yLabel, value: format(p.y) },
                    { label: zLabel, value: String(p.z) },
                    ...(p.extra ?? []),
                  ]}
                />
              )
            }}
          />
          <Scatter
            data={shown}
            cursor={onPointClick ? 'pointer' : undefined}
            onClick={(d: unknown) => {
              const p = (d as { payload?: BreakEvenPoint }).payload
              if (p && onPointClick) onPointClick(p)
            }}
          >
            {shown.map((p) => (
              <Cell key={p.id} fill={groups[p.group]?.color ?? '#64748b'} fillOpacity={0.65} />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}
