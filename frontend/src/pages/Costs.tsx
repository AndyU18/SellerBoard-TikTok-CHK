import { useCallback, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, type Product } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { count, money, presetRange } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'

function History({ productId }: { productId: number }) {
  const history = useAsync(() => api.cogsHistory(productId), [productId])
  if (history.loading) return <p className="text-muted-foreground text-xs">Cargando historial…</p>
  if (history.error) return <p className="text-xs text-red-700">No se pudo cargar el historial: {history.error}</p>
  const rows = history.data ?? []
  if (rows.length === 0)
    return <p className="text-muted-foreground text-xs">Este producto aún no tiene costos registrados.</p>
  return (
    <ul className="text-xs">
      {rows.map((h) => (
        <li key={`${h.valid_from}-${h.component}`} className="flex gap-4">
          <span className="text-muted-foreground w-24">
            {h.valid_from === '2000-01-01' ? 'Desde siempre' : h.valid_from}
          </span>
          <span className="font-medium">{money(h.amount)}</span>
        </li>
      ))}
    </ul>
  )
}

export function Costs() {
  const { can } = useAuth()
  const editable = can('costs.edit')
  const [version, setVersion] = useState(0)
  const [search, setSearch] = useState('')
  const [onlyMissing, setOnlyMissing] = useState(false)
  const [validFrom, setValidFrom] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  const products = useAsync(() => api.products(), [version])
  // Las ventas de la columna "90 d" respetan el canal y la tienda elegidos; el costo es del producto.
  const scope = useScope()
  const sales = useAsync(
    () => api.profit('product', { ...presetRange('90d'), channel: scope.channelParam, account: scope.account }),
    [version, scope.channelParam, scope.account],
  )

  const bySku = useMemo(() => new Map((sales.data?.rows ?? []).map((r) => [r.sku, r])), [sales.data])
  const list = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (products.data ?? [])
      .map((p) => ({ p, sold: bySku.get(p.sku) }))
      .filter(
        ({ p }) => (!onlyMissing || !p.has_cost) && (!q || `${p.sku} ${p.name ?? ''}`.toLowerCase().includes(q)),
      )
      .sort((x, y) => (y.sold?.gross_sales ?? 0) - (x.sold?.gross_sales ?? 0) || x.p.sku.localeCompare(y.p.sku))
  }, [products.data, bySku, search, onlyMissing])

  const sold = (products.data ?? []).filter((p) => (bySku.get(p.sku)?.units ?? 0) > 0)
  const missingSold = sold.filter((p) => !p.has_cost)
  const missingSales = missingSold.reduce((s, p) => s + (bySku.get(p.sku)?.gross_sales ?? 0), 0)
  const totalSales = sold.reduce((s, p) => s + (bySku.get(p.sku)?.gross_sales ?? 0), 0)

  const save = useCallback(
    async (p: Product, value: number) => {
      setSaveError(null)
      setSaved(null)
      try {
        await api.updateCogs(p.id, value, validFrom || undefined)
        setSaved(`${p.sku}: costo ${money(value)} guardado${validFrom ? ` desde ${validFrom}` : ''}.`)
        setVersion((v) => v + 1)
      } catch (e) {
        setSaveError(e instanceof Error ? e.message : 'No se pudo guardar el costo')
      }
    },
    [validFrom],
  )

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Configuración de COGS por producto</h1>

      {missingSold.length > 0 ? (
        <div role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <strong>{missingSold.length} productos vendidos sin costo</strong> ({money(missingSales)}
          {totalSales > 0 && `, ${((missingSales / totalSales) * 100).toFixed(0)}% de las ventas`} en los últimos 90
          días). Mientras no tengan costo, su utilidad aparece inflada.
        </div>
      ) : (
        sold.length > 0 && (
          <div role="status" className="rounded-md border border-green-300 bg-green-50 p-3 text-sm text-green-900">
            Todos los productos vendidos en los últimos 90 días tienen costo.
          </div>
        )
      )}

      {(products.error || saveError) && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          {saveError ?? `No se pudo cargar: ${products.error}`}
        </div>
      )}
      {saved && (
        <p role="status" className="text-sm text-green-700">
          {saved}
        </p>
      )}

      <div className="flex flex-wrap items-end gap-4">
        <Input
          placeholder="Buscar por SKU o nombre"
          aria-label="Buscar producto"
          className="w-64"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} />
          Solo sin costo
        </label>
        {editable && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground text-xs">Vigente desde (opcional)</span>
            <Input type="date" className="w-44" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} />
          </label>
        )}
        {editable && (
          <Button variant="outline" size="sm" onClick={() => setValidFrom('')} disabled={!validFrom}>
            Sin fecha
          </Button>
        )}
      </div>
      {editable && (
        <p className="text-muted-foreground max-w-3xl text-sm">
          Sin fecha: el primer costo de un producto rige desde siempre y los cambios siguientes rigen desde hoy. Con
          fecha, la utilidad se recalcula desde ese día. Para cargar muchos costos a la vez usa Importar → Costos
          (COGS). Se guarda al salir del campo.
        </p>
      )}

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Producto</TableHead>
                <TableHead className="text-right">Unid. (90 d)</TableHead>
                <TableHead className="text-right">Ventas (90 d)</TableHead>
                <TableHead className="text-right">COGS / unid.</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground py-10 text-center">
                    {products.loading ? 'Cargando…' : 'No hay productos con este filtro.'}
                  </TableCell>
                </TableRow>
              )}
              {list.map(({ p, sold: s }) => (
                <TableRow key={p.id} className={!p.has_cost && (s?.units ?? 0) > 0 ? 'bg-amber-50/60' : ''}>
                  <TableCell>
                    <div className="flex min-w-56 flex-col">
                      <span className="font-medium">{p.name ?? p.sku}</span>
                      <span className="text-muted-foreground text-xs">{p.sku}</span>
                      {open === p.id && (
                        <div className="mt-2">
                          <History productId={p.id} />
                        </div>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">{count(s?.units ?? 0)}</TableCell>
                  <TableCell className="text-right">{money(s?.gross_sales ?? 0)}</TableCell>
                  <TableCell className="text-right">
                    {editable ? (
                      <Input
                        key={`${p.id}-${p.cogs_unit}-${version}`}
                        type="number"
                        min={0}
                        step="0.01"
                        defaultValue={p.has_cost ? p.cogs_unit : ''}
                        placeholder="0.00"
                        className="ml-auto h-8 w-28 text-right"
                        aria-label={`COGS de ${p.sku}`}
                        onBlur={(e) => {
                          if (e.target.value === '') return
                          const v = Number(e.target.value)
                          if (!Number.isNaN(v) && v >= 0 && v !== p.cogs_unit) void save(p, v)
                        }}
                      />
                    ) : (
                      money(p.cogs_unit)
                    )}
                  </TableCell>
                  <TableCell>
                    {p.has_cost ? (
                      <span className="rounded bg-green-100 px-2 py-0.5 text-xs text-green-800">Con costo</span>
                    ) : (
                      <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">Sin costo</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Button variant="ghost" size="sm" onClick={() => setOpen(open === p.id ? null : p.id)}>
                      {open === p.id ? 'Ocultar historial' : 'Historial'}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
