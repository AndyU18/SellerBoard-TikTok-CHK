import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { axisNumber, ChartCard, COLOR, EmptyChart, TipBox } from '@/components/charts/kit'
import { count, isoDate } from '@/lib/format'

const shortDate = (offset: number) => {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  return d.toLocaleDateString('es', { day: 'numeric', month: 'short' })
}
const daysFromToday = (iso: string) =>
  Math.round((Date.parse(`${iso}T00:00:00`) - Date.parse(`${isoDate(new Date())}T00:00:00`)) / 86_400_000)

/** Stock proyectado de un SKU hasta llegar a cero, con la fecha en que se agota y (si se conoce) la de pedir. */
export function DepletionChart({
  name,
  sku,
  stock,
  daily,
  orderBy,
  leadDays,
  reorderPoint,
}: {
  name: string
  sku: string
  stock: number
  daily: number
  /** Fecha ISO límite para pedir. */
  orderBy?: string | null
  /** Días que tarda un pedido hecho hoy en llegar. */
  leadDays?: number
  reorderPoint?: number
}) {
  const cover = daily > 0 ? stock / daily : null
  const orderOffset = orderBy ? daysFromToday(orderBy) : null
  const horizon = Math.min(240, Math.max(30, Math.ceil((cover ?? 60) * 1.2), (leadDays ?? 0) + 7, (orderOffset ?? 0) + 7))
  const data = Array.from({ length: horizon + 1 }, (_, day) => ({ day, stock: Math.max(0, Math.round((stock - daily * day) * 10) / 10) }))
  const csv = data.map((p) => ({ dia: p.day, fecha: shortDate(p.day), stock_proyectado: p.stock }))
  const depletesAt = cover != null && cover <= horizon ? Math.round(cover) : null
  return (
    <ChartCard
      title="Proyección de agotamiento"
      subtitle={
        cover == null
          ? `${name} (${sku}) no tiene ventas recientes: el stock no baja.`
          : `${name} (${sku}): ${count(stock)} unidades a ${daily.toFixed(2)} por día se agotan en ${cover.toFixed(0)} días (${shortDate(Math.round(cover))}).`
      }
      csv={csv}
      filename={`proyeccion-${sku}`}
    >
      {stock <= 0 && cover == null ? (
        <EmptyChart>Sin existencias ni ventas para proyectar.</EmptyChart>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={data} margin={{ top: 16, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis type="number" dataKey="day" domain={[0, horizon]} tickFormatter={shortDate} tick={{ fontSize: 11 }} />
            <YAxis tickFormatter={axisNumber} tick={{ fontSize: 11 }} width={48} />
            <Tooltip
              content={({ active, payload }) => {
                const p = active ? (payload?.[0]?.payload as { day: number; stock: number } | undefined) : undefined
                return p ? <TipBox title={`${shortDate(p.day)} (día ${p.day})`} rows={[{ label: 'Stock proyectado', value: count(p.stock), color: COLOR.blue }]} /> : null
              }}
            />
            <Area type="linear" dataKey="stock" stroke={COLOR.blue} fill={COLOR.blue} fillOpacity={0.15} strokeWidth={2} />
            {reorderPoint != null && reorderPoint > 0 && (
              <ReferenceLine y={reorderPoint} stroke={COLOR.amber} strokeDasharray="4 4" label={{ value: 'Punto de pedido', fontSize: 10, position: 'insideTopRight' }} />
            )}
            {depletesAt != null && (
              <ReferenceLine x={depletesAt} stroke={COLOR.bad} strokeDasharray="4 4" label={{ value: `Se agota ${shortDate(depletesAt)}`, fontSize: 10, position: 'top', fill: COLOR.bad }} />
            )}
            {orderOffset != null && orderOffset >= 0 && orderOffset <= horizon && (
              <ReferenceLine x={orderOffset} stroke={COLOR.good} strokeDasharray="4 4" label={{ value: `Pedir ${shortDate(orderOffset)}`, fontSize: 10, position: 'insideTopLeft', fill: COLOR.good }} />
            )}
            {leadDays != null && leadDays <= horizon && (
              <ReferenceLine x={leadDays} stroke="#8b5cf6" strokeDasharray="2 4" label={{ value: 'Llega si pides hoy', fontSize: 10, position: 'insideBottomRight', fill: '#8b5cf6' }} />
            )}
          </AreaChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  )
}
