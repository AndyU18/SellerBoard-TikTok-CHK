import { CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts'

import { EmptyChart, PALETTE, TipBox } from './kit'

export type BubblePoint = {
  id: string
  label: string
  x: number
  y: number
  /** Tamaño de la burbuja (por ejemplo ventas). */
  z?: number
  /** Grupo para el color (por ejemplo canal). */
  group?: string
}

/** Dispersión con burbujas. Clic en un punto avisa con `onPointClick`; las líneas de referencia marcan umbrales. */
export function BubbleChart({
  points,
  xLabel,
  yLabel,
  zLabel = 'Tamaño',
  xFormat,
  yFormat,
  zFormat,
  xRef,
  yRef,
  height = 340,
  onPointClick,
}: {
  points: BubblePoint[]
  xLabel: string
  yLabel: string
  zLabel?: string
  xFormat: (v: number) => string
  yFormat: (v: number) => string
  zFormat?: (v: number) => string
  xRef?: number
  yRef?: number
  height?: number
  onPointClick?: (p: BubblePoint) => void
}) {
  if (!points.length) return <EmptyChart />
  const groups = [...new Set(points.map((p) => p.group ?? ''))]
  return (
    <div>
      {groups.length > 1 && (
        <div className="mb-2 flex flex-wrap gap-3 text-xs">
          {groups.map((g, i) => (
            <span key={g} className="flex items-center gap-1.5">
              <span className="inline-block size-2.5 rounded-full" style={{ background: PALETTE[i % PALETTE.length] }} />
              {g || 'Sin grupo'}
            </span>
          ))}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis type="number" dataKey="x" name={xLabel} tickFormatter={xFormat} tick={{ fontSize: 11 }} label={{ value: xLabel, position: 'insideBottom', offset: -4, fontSize: 11 }} />
          <YAxis type="number" dataKey="y" name={yLabel} tickFormatter={yFormat} tick={{ fontSize: 11 }} width={60} label={{ value: yLabel, angle: -90, position: 'insideLeft', fontSize: 11 }} />
          <ZAxis type="number" dataKey="z" range={[60, 600]} name={zLabel} />
          {xRef != null && <ReferenceLine x={xRef} stroke="#94a3b8" strokeDasharray="4 4" />}
          {yRef != null && <ReferenceLine y={yRef} stroke="#94a3b8" strokeDasharray="4 4" />}
          <Tooltip
            cursor={{ strokeDasharray: '3 3' }}
            content={({ active, payload }) => {
              const p = active ? (payload?.[0]?.payload as BubblePoint | undefined) : undefined
              if (!p) return null
              const rows = [
                { label: xLabel, value: xFormat(p.x) },
                { label: yLabel, value: yFormat(p.y) },
              ]
              if (p.z != null) rows.push({ label: zLabel, value: (zFormat ?? String)(p.z) })
              return <TipBox title={p.label} rows={rows} />
            }}
          />
          <Scatter data={points} cursor={onPointClick ? 'pointer' : undefined} onClick={(d: unknown) => {
            const p = (d as { payload?: BubblePoint }).payload
            if (p && onPointClick) onPointClick(p)
          }}>
            {points.map((p) => (
              <Cell key={p.id} fill={PALETTE[groups.indexOf(p.group ?? '') % PALETTE.length]} fillOpacity={0.65} />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}
