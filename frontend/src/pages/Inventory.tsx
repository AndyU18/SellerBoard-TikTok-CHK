import { useMemo, useState } from 'react'

import { ChannelOnlyNotice } from '@/components/ScopeBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, type Filters, type StockStatus } from '@/lib/api'
import { count, money } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

const STATUS: Record<StockStatus, { label: string; badge: string }> = {
  out_of_stock: { label: 'Agotado y vendiéndose', badge: 'bg-red-100 text-red-800' },
  unpublished: { label: 'Sin publicar', badge: 'bg-amber-100 text-amber-800' },
  low: { label: 'Por agotarse', badge: 'bg-orange-100 text-orange-800' },
  no_stock: { label: 'Sin existencias', badge: 'bg-slate-100 text-slate-700' },
  no_sales: { label: 'Sin ventas', badge: 'bg-slate-100 text-slate-700' },
  ok: { label: 'Bien', badge: 'bg-green-100 text-green-800' },
}

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

export function Inventory() {
  const scope = useScope()
  const [search, setSearch] = useState('')
  const [only, setOnly] = useState<StockStatus | 'all'>('all')
  const filters = useMemo<Filters>(
    () => ({ channel: scope.channelParam, account: scope.account }) as Filters,
    [scope.channelParam, scope.account],
  )
  const data = useAsync(() => api.inventory(filters), [filters])
  const sm = data.data?.summary

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (data.data?.rows ?? []).filter(
      (r) => (only === 'all' || r.status === only) && (!q || `${r.sku} ${r.product_name ?? ''}`.toLowerCase().includes(q)),
    )
  }, [data.data, search, only])

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Inventario y publicaciones</h1>
      <ChannelOnlyNotice channels={['walmart']} what="El inventario" />

      {data.error && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          No se pudo cargar: {data.error}
        </div>
      )}

      {(sm?.out_of_stock ?? 0) > 0 && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          <strong>{count(sm?.out_of_stock ?? 0)} productos agotados que se estaban vendiendo.</strong> Cada día sin
          existencias dejas de vender cerca de {money(sm?.daily_sales_at_risk ?? 0)}.
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi
          title="Agotados"
          value={count(sm?.out_of_stock ?? 0)}
          hint="con ventas recientes y 0 existencias"
          className={(sm?.out_of_stock ?? 0) > 0 ? 'text-red-600' : ''}
        />
        <Kpi
          title="Por agotarse"
          value={count(sm?.low ?? 0)}
          hint={`menos de ${data.data?.low_cover_days ?? 14} días de inventario`}
          className={(sm?.low ?? 0) > 0 ? 'text-orange-600' : ''}
        />
        <Kpi
          title="Sin publicar"
          value={count(sm?.unpublished ?? 0)}
          hint="no se pueden comprar; revisa el motivo"
          className={(sm?.unpublished ?? 0) > 0 ? 'text-amber-600' : ''}
        />
        <Kpi
          title="Inventario a precio de venta"
          value={money(sm?.stock_retail_value ?? 0)}
          hint={`${count(sm?.units_in_stock ?? 0)} unidades · ${count(sm?.no_sales ?? 0)} productos con stock sin ventas`}
        />
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <Input
          placeholder="Buscar por SKU o nombre"
          aria-label="Buscar producto"
          className="w-64"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="flex flex-wrap gap-1">
          {(['all', 'out_of_stock', 'low', 'unpublished', 'no_sales', 'ok'] as const).map((s) => (
            <button
              key={s}
              onClick={() => setOnly(s)}
              className={`rounded-md border px-3 py-1.5 text-sm ${only === s ? 'bg-slate-800 text-white' : 'hover:bg-slate-100'}`}
            >
              {s === 'all' ? 'Todos' : STATUS[s].label}
            </button>
          ))}
        </div>
      </div>

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Producto</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead className="text-right">Disponible</TableHead>
                <TableHead className="text-right">Vendidas ({data.data?.days ?? 30} d)</TableHead>
                <TableHead className="text-right">Días de inventario</TableHead>
                <TableHead className="text-right">Precio</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground py-10 text-center">
                    {data.loading ? 'Cargando…' : 'Sin productos. Sincroniza Walmart desde Importar.'}
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r) => (
                <TableRow key={`${r.account}-${r.sku}`}>
                  <TableCell>
                    <div className="flex min-w-56 flex-col">
                      <span className="font-medium">{r.product_name ?? r.sku}</span>
                      <span className="text-muted-foreground text-xs">
                        {r.sku} · {r.account}
                      </span>
                      {r.unpublished_reasons && <span className="text-xs text-amber-700">{r.unpublished_reasons}</span>}
                    </div>
                  </TableCell>
                  <TableCell>
                    <span className={`rounded px-2 py-0.5 text-xs ${STATUS[r.status].badge}`}>{STATUS[r.status].label}</span>
                    {r.published_status && r.status === 'unpublished' && (
                      <div className="text-muted-foreground mt-1 text-xs">{r.published_status}</div>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {r.avail_to_sell == null ? '—' : count(r.avail_to_sell)}
                    {(r.reserved ?? 0) > 0 && <span className="text-muted-foreground text-xs"> (+{r.reserved} reserv.)</span>}
                  </TableCell>
                  <TableCell className="text-right">{count(r.units_sold)}</TableCell>
                  <TableCell className="text-right">{r.days_of_cover == null ? '—' : `${r.days_of_cover.toFixed(0)} d`}</TableCell>
                  <TableCell className="text-right">{r.price == null ? '—' : money(r.price)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <p className="text-muted-foreground max-w-3xl text-xs">
        Existencias y estado de publicación vienen de Walmart; las ventas, de tus órdenes. Los días de inventario son
        las existencias entre el ritmo de venta diario del periodo. Un producto “Sin publicar” con motivo de sistema
        suele necesitar corrección en el Seller Center, aunque tenga unidades disponibles.
      </p>
    </div>
  )
}
