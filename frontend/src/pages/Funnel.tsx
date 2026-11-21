import { useMemo, useState } from 'react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, type Filters } from '@/lib/api'
import { count, money, percent, presetRange, type Preset } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

// El tráfico de TikTok solo se guarda hasta 90 días atrás.
const RANGES: { value: Preset; label: string }[] = [
  { value: '30d', label: 'Últimos 30 días' },
  { value: '90d', label: 'Últimos 90 días' },
]

const SOURCE_LABEL: Record<string, string> = { VIDEO: 'Videos', LIVE: 'LIVE', PRODUCT_CARD: 'Tarjeta de producto' }

function Kpi({ title, value, hint }: { title: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold">{value}</div>
        {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
      </CardContent>
    </Card>
  )
}

export function Funnel() {
  const [preset, setPreset] = useState<Preset>('30d')
  const filters = useMemo<Filters>(() => presetRange(preset), [preset])
  const data = useAsync(() => api.funnel(filters), [filters])

  const t = data.data?.totals
  const max = t?.impressions || 1
  const steps = t
    ? [
        { label: 'Impresiones de producto', value: t.impressions, note: '' },
        { label: 'Vistas de la página del producto', value: t.page_views, note: `CTR ${percent(t.ctr)}` },
        { label: 'Compradores', value: t.buyers, note: `conversión ${percent(t.conversion)}` },
      ]
    : []

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Embudo de tráfico y conversión</h1>
        <Select items={RANGES} value={preset} onValueChange={(v) => v && setPreset(v as Preset)}>
          <SelectTrigger className="w-44" aria-label="Periodo">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {RANGES.map((p) => (
              <SelectItem key={p.value} value={p.value}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {data.error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {data.error}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi title="Tasa de clics (CTR)" value={percent(t?.ctr)} hint="vistas de producto / impresiones" />
        <Kpi title="Conversión" value={percent(t?.conversion)} hint="compradores / vistas de producto" />
        <Kpi title="Ventas (GMV)" value={money(t?.gmv ?? 0)} hint={`${count(t?.orders ?? 0)} órdenes`} />
        <Kpi title="Ticket promedio" value={money(t?.aov)} hint="GMV / órdenes" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Embudo</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {steps.length === 0 && (
            <p className="text-muted-foreground text-sm">
              {data.loading ? 'Cargando…' : 'Sin tráfico. Sincroniza TikTok Shop desde Importar.'}
            </p>
          )}
          {steps.map((s) => (
            <div key={s.label}>
              <div className="mb-1 flex justify-between text-sm">
                <span>{s.label}</span>
                <span>
                  <span className="font-medium">{count(s.value)}</span>
                  {s.note && <span className="text-muted-foreground ml-2 text-xs">{s.note}</span>}
                </span>
              </div>
              <div className="bg-muted h-6 rounded">
                {/* Escala raíz cuadrada: con una escala lineal los compradores serían una línea invisible. */}
                <div
                  className="h-6 rounded bg-blue-500"
                  style={{ width: `${Math.max(2, Math.sqrt(s.value / max) * 100)}%` }}
                />
              </div>
            </div>
          ))}
          {steps.length > 0 && (
            <p className="text-muted-foreground text-xs">
              Las barras usan escala de raíz cuadrada para que se vean los tres pasos; los números son exactos.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Por origen del tráfico</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Origen</TableHead>
                <TableHead className="text-right">Impresiones</TableHead>
                <TableHead className="text-right">Vistas</TableHead>
                <TableHead className="text-right">CTR</TableHead>
                <TableHead className="text-right">Compradores</TableHead>
                <TableHead className="text-right">Conversión</TableHead>
                <TableHead className="text-right">Ventas</TableHead>
                <TableHead className="text-right">% de ventas</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data.data?.by_source ?? []).map((s) => (
                <TableRow key={s.source}>
                  <TableCell className="font-medium">{SOURCE_LABEL[s.source] ?? s.source}</TableCell>
                  <TableCell className="text-right">{count(s.impressions)}</TableCell>
                  <TableCell className="text-right">{count(s.page_views)}</TableCell>
                  <TableCell className="text-right">{percent(s.ctr)}</TableCell>
                  <TableCell className="text-right">{count(s.buyers)}</TableCell>
                  <TableCell className="text-right">{percent(s.conversion)}</TableCell>
                  <TableCell className="text-right">{money(s.gmv)}</TableCell>
                  <TableCell className="text-right">{percent(s.gmv_share)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Vistas de producto y conversión por día</CardTitle>
        </CardHeader>
        <CardContent className="h-72">
          {(data.data?.daily.length ?? 0) === 0 ? (
            <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
              {data.loading ? 'Cargando…' : 'Sin datos para este filtro.'}
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={(data.data?.daily ?? []).map((d) => ({ ...d, conv: (d.conversion ?? 0) * 100 }))}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(5)} />
                <YAxis yAxisId="v" width={56} />
                <YAxis yAxisId="c" orientation="right" tickFormatter={(v: number) => `${v}%`} width={44} />
                <Tooltip formatter={(v, name) => (name === 'Conversión' ? `${Number(v).toFixed(2)}%` : count(Number(v)))} />
                <Legend />
                <Bar yAxisId="v" dataKey="page_views" name="Vistas de producto" fill="#3b82f6" />
                <Line yAxisId="c" dataKey="conv" name="Conversión" stroke="#f59e0b" dot={false} strokeWidth={2} />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <p className="text-muted-foreground max-w-3xl text-xs">
        Datos de la API de analytics de TikTok Shop (hasta 90 días atrás). Los compradores se suman por día, así que una
        persona que compró en dos días cuenta dos veces. El tráfico de la tienda es de todos los canales de TikTok, no
        solo de tus órdenes sincronizadas.
      </p>
    </div>
  )
}
