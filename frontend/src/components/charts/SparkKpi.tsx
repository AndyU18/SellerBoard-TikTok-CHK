import { Card, CardContent } from '@/components/ui/card'

/** Línea pequeña sin ejes (para tarjetas y filas de tabla). */
export function Sparkline({
  values,
  width = 96,
  height = 28,
  color = '#3b82f6',
}: {
  values: number[]
  width?: number
  height?: number
  color?: string
}) {
  if (values.length < 2) return <span className="text-muted-foreground text-xs">—</span>
  const min = Math.min(...values)
  const span = Math.max(...values) - min || 1
  const step = width / (values.length - 1)
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(height - 3 - ((v - min) / span) * (height - 6)).toFixed(1)}`)
  const last = pts[pts.length - 1].split(',')
  return (
    <svg width={width} height={height} aria-hidden="true">
      <polyline points={pts.join(' ')} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r={2.5} fill={color} />
    </svg>
  )
}

/** Indicador con mini-gráfico y variación contra el período anterior (`delta` = 0.12 → +12%). */
export function SparkKpi({
  title,
  value,
  delta,
  spark,
  hint,
  goodWhenUp = true,
}: {
  title: string
  value: string
  delta?: number | null
  spark?: number[]
  hint?: string
  /** En costos, que suban es malo: pasar false para invertir el color de la variación. */
  goodWhenUp?: boolean
}) {
  const up = (delta ?? 0) >= 0
  const good = delta == null ? null : up === goodWhenUp
  return (
    <Card>
      <CardContent className="flex items-end justify-between gap-2 pt-4">
        <div>
          <div className="text-muted-foreground text-xs font-medium">{title}</div>
          <div className="text-2xl font-semibold tabular-nums">{value}</div>
          {delta != null && (
            <div className={`text-xs font-medium ${good ? 'text-emerald-600' : 'text-red-600'}`}>
              {up ? '▲' : '▼'} {Math.abs(delta * 100).toFixed(1)}% vs período anterior
            </div>
          )}
          {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
        </div>
        {spark && <Sparkline values={spark} color={good === false ? '#ef4444' : '#3b82f6'} />}
      </CardContent>
    </Card>
  )
}
