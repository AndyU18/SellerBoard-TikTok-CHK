import { useMemo, useState } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { api, type Filters } from '@/lib/api'
import { useScope } from '@/lib/scope'
import { count, money, percent, PRESETS, presetRange, tone, type Preset } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const TYPE_LABEL: Record<string, string> = {
  VIDEO: 'Video',
  LIVE: 'LIVE',
  SHOP: 'Tienda del creador',
  LINKSHARE: 'Enlace compartido',
}

type Sort = 'gmv' | 'profit'

function Kpi({ title, value, hint, className = '' }: { title: string; value: string; hint?: string; className?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-semibold ${className}`}>{value}</div>
        {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
      </CardContent>
    </Card>
  )
}

export function Creatives() {
  const [preset, setPreset] = useState<Preset>('90d')
  const [type, setType] = useState<string>('all')
  const [sort, setSort] = useState<Sort>('gmv')
  const scope = useScope()
  const filters = useMemo<Filters>(
    () => ({ ...presetRange(preset), channel: scope.channelParam, account: scope.account }),
    [preset, scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.creatives(filters), [filters])

  const sm = data.data?.summary
  const rows = useMemo(
    () =>
      (data.data?.contents ?? [])
        .filter((c) => type === 'all' || c.content_type === type)
        .sort((a, b) => b[sort] - a[sort]),
    [data.data, type, sort],
  )

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Creativos y videos virales</h1>
        <Select items={PRESETS} value={preset} onValueChange={(v) => v && setPreset(v as Preset)}>
          <SelectTrigger className="w-44" aria-label="Periodo">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PRESETS.map((p) => (
              <SelectItem key={p.value} value={p.value}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <ChannelOnlyNotice channels={['tiktok_shop']} what="Creativos y videos virales" />

      {data.error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {data.error}
        </div>
      )}

      {sm && sm.orders_pending > 0 && (
        <div role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <strong>{count(sm.orders_pending)} órdenes de afiliado aún no se liquidan.</strong> Su utilidad no se puede
          calcular todavía, así que la utilidad de los videos recientes aparece baja. Las ventas sí están completas.
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Ventas por creadores"
          value={money(sm?.gmv ?? 0)}
          hint={`${count(sm?.orders ?? 0)} órdenes · ${count(sm?.contents ?? 0)} contenidos · ${count(sm?.creators ?? 0)} creadores`}
        />
        <Kpi
          title="Utilidad liquidada"
          value={money(sm?.profit ?? 0)}
          hint={`comisiones de afiliado ya descontadas (${money(sm?.commission ?? 0)})`}
          className={tone(sm?.profit)}
        />
        <Kpi
          title="Concentración top 10"
          value={percent(sm?.top10_share)}
          hint="de las ventas viene de los 10 mejores contenidos"
        />
        <Kpi
          title="Contenidos con pérdida"
          value={count(sm?.losing_contents ?? 0)}
          hint="utilidad negativa en lo ya liquidado"
          className={(sm?.losing_contents ?? 0) > 0 ? 'text-red-600' : ''}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Por tipo de contenido</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tipo</TableHead>
                <TableHead className="text-right">Contenidos</TableHead>
                <TableHead className="text-right">Órdenes</TableHead>
                <TableHead className="text-right">Ventas</TableHead>
                <TableHead className="text-right">Utilidad</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data.data?.by_type ?? []).map((t) => (
                <TableRow key={t.content_type}>
                  <TableCell>{TYPE_LABEL[t.content_type] ?? t.content_type}</TableCell>
                  <TableCell className="text-right">{count(t.contents)}</TableCell>
                  <TableCell className="text-right">{count(t.orders)}</TableCell>
                  <TableCell className="text-right">{money(t.gmv)}</TableCell>
                  <TableCell className={`text-right ${tone(t.profit)}`}>{money(t.profit)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center gap-4">
        <Tabs value={type} onValueChange={setType}>
          <TabsList>
            <TabsTrigger value="all">Todos</TabsTrigger>
            <TabsTrigger value="VIDEO">Videos</TabsTrigger>
            <TabsTrigger value="LIVE">LIVE</TabsTrigger>
          </TabsList>
        </Tabs>
        <Tabs value={sort} onValueChange={(v) => setSort(v as Sort)}>
          <TabsList>
            <TabsTrigger value="gmv">Ordenar por ventas</TabsTrigger>
            <TabsTrigger value="profit">Ordenar por utilidad</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Contenido</TableHead>
                <TableHead>Creador</TableHead>
                <TableHead>Producto</TableHead>
                <TableHead className="text-right">Vistas</TableHead>
                <TableHead className="text-right">Órdenes</TableHead>
                <TableHead className="text-right">Ventas</TableHead>
                <TableHead className="text-right">Comisión</TableHead>
                <TableHead className="text-right">Utilidad</TableHead>
                <TableHead className="text-right">Margen</TableHead>
                <TableHead>Periodo</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={10} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : 'Sin ventas de creadores. Sincroniza TikTok Shop desde Importar.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((c) => (
                <TableRow key={`${c.content_type}-${c.content_id}`}>
                  <TableCell>
                    <div className="flex flex-col">
                      {c.url ? (
                        <a href={c.url} target="_blank" rel="noreferrer" className="font-medium text-blue-700 hover:underline">
                          Video …{c.content_id.slice(-6)}
                        </a>
                      ) : (
                        <span className="font-medium">
                          {TYPE_LABEL[c.content_type] ?? c.content_type} …{c.content_id.slice(-6)}
                        </span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    @{c.creator ?? '—'}
                    {c.sampled && (
                      <span className="ml-2 rounded bg-green-100 px-1.5 py-0.5 text-xs text-green-800" title="Recibió una muestra tuya">
                        muestra
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-xs">{c.top_sku ?? '—'}</TableCell>
                  <TableCell className="text-right">
                    {c.views == null ? (
                      '—'
                    ) : (
                      <>
                        {count(c.views)}
                        <span className="text-muted-foreground block text-xs">CTR {percent(c.ctr)}</span>
                      </>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {count(c.orders)}
                    {c.orders_pending > 0 && (
                      <span className="text-muted-foreground text-xs" title="Órdenes aún sin liquidar">
                        {' '}
                        ({c.orders_pending} sin liquidar)
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">{money(c.gmv)}</TableCell>
                  <TableCell className="text-right">{money(c.commission)}</TableCell>
                  <TableCell className={`text-right font-medium ${tone(c.profit)}`}>{money(c.profit)}</TableCell>
                  <TableCell className={`text-right ${tone(c.margin)}`}>{percent(c.margin)}</TableCell>
                  <TableCell className="text-muted-foreground text-xs">
                    {c.first_order} → {c.last_order}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <p className="text-muted-foreground max-w-3xl text-xs">
        Cada venta de afiliado trae el video, LIVE o enlace que la originó. Si una orden viene de varios contenidos, su
        utilidad se reparte según la base de comisión de cada uno. Ventas = base de comisión de TikTok. Las vistas y el
        CTR son de los últimos 60 días y solo de los 300 videos con más ventas. No incluye gasto en anuncios (Spark
        Ads): para eso hace falta autorizar TikTok Ads.
      </p>
    </div>
  )
}
