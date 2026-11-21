import { useMemo, useState } from 'react'

import { FilterBar } from '@/components/FilterBar'
import { useScope } from '@/lib/scope'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, type DaypartCell, type Filters } from '@/lib/api'
import { count, money, presetRange, type Preset } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

type Metric = 'orders' | 'gross_sales'

const DAYS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
const HOURS = Array.from({ length: 24 }, (_, h) => h)
const hourLabel = (h: number) => `${String(h).padStart(2, '0')}:00`

export function Dayparting() {
  const [preset, setPreset] = useState<Preset>('90d')
  const scope = useScope()
  const [metric, setMetric] = useState<Metric>('orders')

  const filters = useMemo<Filters>(
    () => ({ ...presetRange(preset), channel: scope.channelParam, account: scope.account }),
    [preset, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.dayparting(filters), [filters])

  const cells = useMemo(() => {
    const m = new Map<string, DaypartCell>()
    for (const c of data.data?.cells ?? []) m.set(`${c.weekday}-${c.hour}`, c)
    return m
  }, [data.data])
  const max = Math.max(1, ...(data.data?.cells ?? []).map((c) => c[metric]))
  const fmt = (v: number) => (metric === 'orders' ? count(v) : money(v))

  const coverage = data.data?.coverage
  const missing = coverage ? coverage.total - coverage.with_time : 0

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Dayparting: horas pico</h1>
        <FilterBar preset={preset} onPreset={setPreset} />
      </div>

      {data.error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {data.error}
        </div>
      )}

      {coverage && missing > 0 && (
        <div role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          {count(missing)} de {count(coverage.total)} órdenes no tienen hora registrada (vienen de un CSV o se
          sincronizaron antes de esta versión). Vuelve a sincronizar TikTok y Walmart desde Importar para completarlas.
        </div>
      )}

      <Tabs value={metric} onValueChange={(v) => setMetric(v as Metric)}>
        <TabsList>
          <TabsTrigger value="orders">Por órdenes</TabsTrigger>
          <TabsTrigger value="gross_sales">Por ventas</TabsTrigger>
        </TabsList>
      </Tabs>

      <div className="grid gap-4 md:grid-cols-3">
        {(data.data?.best_hours ?? []).map((b, i) => (
          <Card key={b.hour}>
            <CardHeader>
              <CardTitle className="text-sm font-medium">
                #{i + 1} hora pico · {hourLabel(b.hour)}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-semibold">{count(b.orders)} órdenes</div>
              <div className="text-muted-foreground text-xs">
                {money(b.gross_sales)} en ventas · {count(b.units)} unidades
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            Mapa de calor ({metric === 'orders' ? 'órdenes' : 'ventas'}) · zona horaria {data.data?.timezone ?? '—'}
          </CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {(data.data?.cells.length ?? 0) === 0 ? (
            <p className="text-muted-foreground py-10 text-center text-sm">
              {data.loading ? 'Cargando…' : 'Sin órdenes con hora registrada en este filtro.'}
            </p>
          ) : (
            <div className="min-w-[44rem]">
              <div className="grid grid-cols-[3rem_repeat(24,minmax(0,1fr))] gap-0.5 text-center text-[10px]">
                <div />
                {HOURS.map((h) => (
                  <div key={h} className="text-muted-foreground">
                    {h}
                  </div>
                ))}
                {DAYS.map((day, wd) => (
                  <div key={day} className="contents">
                    <div className="text-muted-foreground flex items-center text-xs">{day}</div>
                    {HOURS.map((h) => {
                      const c = cells.get(`${wd}-${h}`)
                      const v = c?.[metric] ?? 0
                      return (
                        <div
                          key={h}
                          className="h-8 rounded-sm"
                          style={{
                            backgroundColor: v
                              ? `rgba(37, 99, 235, ${0.12 + 0.88 * (v / max)})`
                              : 'rgba(148, 163, 184, 0.12)',
                          }}
                          title={`${day} ${hourLabel(h)} · ${count(c?.orders ?? 0)} órdenes · ${money(c?.gross_sales ?? 0)}`}
                          aria-label={`${day} ${hourLabel(h)}: ${fmt(v)}`}
                        />
                      )
                    })}
                  </div>
                ))}
              </div>
              <p className="text-muted-foreground mt-3 text-xs">
                Más oscuro = más {metric === 'orders' ? 'órdenes' : 'ventas'}. Pasa el cursor sobre una celda para ver el
                detalle.
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
