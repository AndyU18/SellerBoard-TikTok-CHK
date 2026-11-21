import { useMemo, useState } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, type Filters } from '@/lib/api'
import { count, money, percent, presetRange, type Preset } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

// TikTok solo entrega LIVE de los últimos 90 días.
const RANGES: { value: Preset; label: string }[] = [
  { value: '30d', label: 'Últimos 30 días' },
  { value: '90d', label: 'Últimos 90 días' },
]

const minutes = (v: number | null | undefined) => {
  if (v == null) return '—'
  const h = Math.floor(v / 60)
  return h > 0 ? `${h} h ${Math.round(v % 60)} min` : `${Math.round(v)} min`
}

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

export function Live() {
  const [preset, setPreset] = useState<Preset>('90d')
  const filters = useMemo<Filters>(() => presetRange(preset), [preset])
  const data = useAsync(() => api.live(filters), [filters])
  const sm = data.data?.summary

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">LIVE shopping</h1>
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

      {sm && sm.sessions > 0 && sm.sessions_with_sales < sm.sessions / 2 && (
        <div role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <strong>
            {count(sm.sessions - sm.sessions_with_sales)} de {count(sm.sessions)} transmisiones no vendieron nada.
          </strong>{' '}
          El LIVE aporta muy poco frente a los videos (ver Embudo): la mayoría de las sesiones son de creadores con
          audiencias pequeñas.
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Ventas por LIVE"
          value={money(sm?.gmv ?? 0)}
          hint={`${count(sm?.orders ?? 0)} órdenes · ${count(sm?.customers ?? 0)} compradores`}
        />
        <Kpi
          title="Transmisiones"
          value={count(sm?.sessions ?? 0)}
          hint={`${count(sm?.sessions_with_sales ?? 0)} con ventas · duración prom. ${minutes(sm?.avg_duration_min)}`}
        />
        <Kpi
          title="Ventas por hora en vivo"
          value={money(sm?.gmv_per_hour)}
          hint={`${money(sm?.avg_gmv_per_session)} por transmisión en promedio`}
        />
        <Kpi
          title="Concentración top 5"
          value={percent(sm?.top5_share)}
          hint="de las ventas vienen de las 5 mejores sesiones"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
        <Card>
          <CardHeader>
            <CardTitle>Mejores transmisiones</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Creador</TableHead>
                  <TableHead>Inicio</TableHead>
                  <TableHead className="text-right">Duración</TableHead>
                  <TableHead className="text-right">Órdenes</TableHead>
                  <TableHead className="text-right">Ventas</TableHead>
                  <TableHead className="text-right">Por hora</TableHead>
                  <TableHead className="text-right">Clic a orden</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data.data?.top_sessions ?? []).length === 0 && (
                  <TableRow>
                    <TableCell colSpan={7} className="text-muted-foreground py-10 text-center">
                      {data.loading ? 'Cargando…' : 'Sin transmisiones. Sincroniza TikTok Shop desde Importar.'}
                    </TableCell>
                  </TableRow>
                )}
                {(data.data?.top_sessions ?? []).map((s) => (
                  <TableRow key={s.external_id}>
                    <TableCell>
                      <div className="flex flex-col">
                        <span className="font-medium">@{s.username ?? '—'}</span>
                        {s.title && <span className="text-muted-foreground max-w-48 truncate text-xs">{s.title}</span>}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">{s.start_at.slice(0, 16).replace('T', ' ')}</TableCell>
                    <TableCell className="text-right">{minutes(s.duration_min)}</TableCell>
                    <TableCell className="text-right">{count(s.orders)}</TableCell>
                    <TableCell className="text-right font-medium">{money(s.gmv)}</TableCell>
                    <TableCell className="text-right">{money(s.gmv_per_hour)}</TableCell>
                    <TableCell className="text-right">{percent(s.click_to_order_rate)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Creadores con más ventas en LIVE</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="divide-y text-sm">
              {(data.data?.by_creator ?? []).map((c) => (
                <li key={c.username} className="flex items-center justify-between gap-3 py-1.5">
                  <span className="min-w-0 truncate">
                    @{c.username}
                    <span className="text-muted-foreground ml-2 text-xs">{count(c.sessions)} sesiones</span>
                  </span>
                  <span className="shrink-0 font-medium">{money(c.gmv)}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>

      <p className="text-muted-foreground max-w-3xl text-xs">
        Incluye las transmisiones de tus creadores afiliados. TikTok entrega hasta 600 sesiones por sincronización,
        ordenadas por ventas, y solo los últimos 90 días. No incluye espectadores ni gasto de promoción del LIVE
        (para eso hace falta autorizar TikTok Ads). Las horas en vivo suman la duración de todas las sesiones.
      </p>
    </div>
  )
}
