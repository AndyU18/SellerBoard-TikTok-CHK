import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { money } from '@/lib/format'

import { axisMoney, COLOR, EmptyChart, TipBox } from './kit'

export type WaterfallStep = {
  id: string
  label: string
  /** `total` arranca o cierra la cascada (valor absoluto); `delta` suma o resta al acumulado. */
  kind: 'total' | 'delta'
  value: number
}

type Bar3 = WaterfallStep & { range: [number, number]; end: number }

/** Cascada de, por ejemplo, ventas → utilidad. Clic en una barra para abrir el detalle de ese concepto. */
export function Waterfall({
  steps,
  height = 320,
  onStepClick,
}: {
  steps: WaterfallStep[]
  height?: number
  onStepClick?: (step: WaterfallStep) => void
}) {
  if (!steps.length) return <EmptyChart />
  let running = 0
  const data: Bar3[] = steps.map((s) => {
    if (s.kind === 'total') {
      running = s.value
      return { ...s, range: [Math.min(0, s.value), Math.max(0, s.value)], end: s.value }
    }
    const start = running
    running += s.value
    return { ...s, range: [Math.min(start, running), Math.max(start, running)], end: running }
  })
  const fill = (s: Bar3) => (s.kind === 'total' ? COLOR.blue : s.value >= 0 ? COLOR.good : COLOR.bad)

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 11 }} interval={0} />
        <YAxis tickFormatter={axisMoney} tick={{ fontSize: 11 }} width={56} />
        <Tooltip
          content={({ active, payload }) => {
            const d = active ? (payload?.[0]?.payload as Bar3 | undefined) : undefined
            return d ? (
              <TipBox
                title={d.label}
                rows={[
                  { label: d.kind === 'total' ? 'Total' : 'Cambio', value: money(d.value) },
                  { label: 'Acumulado', value: money(d.end) },
                ]}
              />
            ) : null
          }}
        />
        <Bar dataKey="range" radius={2} cursor={onStepClick ? 'pointer' : undefined} onClick={(d: unknown) => {
          const step = (d as { payload?: Bar3 }).payload
          if (step && onStepClick) onStepClick(step)
        }}>
          {data.map((d) => (
            <Cell key={d.id} fill={fill(d)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
