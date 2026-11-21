import { useCallback, useMemo, useState } from 'react'

import { FilterBar } from '@/components/FilterBar'
import { useScope } from '@/lib/scope'
import { ProfitTable, type CogsInfo } from '@/components/ProfitTable'
import { Card, CardContent } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, type Filters, type GroupBy } from '@/lib/api'
import { presetRange, type Preset } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'

const VIEWS: { value: GroupBy; label: string }[] = [
  { value: 'product', label: 'Por producto' },
  { value: 'channel', label: 'Por canal' },
  { value: 'channel_product', label: 'Canal × producto' },
  { value: 'account', label: 'Por cuenta' },
]

export function Products() {
  const [groupBy, setGroupBy] = useState<GroupBy>('channel_product')
  const [preset, setPreset] = useState<Preset>('90d')
  const scope = useScope()
  const [version, setVersion] = useState(0)
  const [saveError, setSaveError] = useState<string | null>(null)

  const filters = useMemo<Filters>(
    () => ({ ...presetRange(preset), channel: scope.channelParam, account: scope.account }),
    [preset, scope.channelParam, scope.account],
  )

  const profit = useAsync(() => api.profit(groupBy, filters), [groupBy, filters, version])
  const products = useAsync(() => api.products(), [version])

  const cogsBySku = useMemo(() => {
    const map: Record<string, CogsInfo> = {}
    for (const p of products.data ?? []) map[p.sku] = { id: p.id, cogs: p.cogs_unit }
    return map
  }, [products.data])

  const onCogsChange = useCallback(async (id: number, cogs: number) => {
    setSaveError(null)
    try {
      await api.updateCogs(id, cogs)
      setVersion((v) => v + 1)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'No se pudo guardar el COGS')
    }
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Productos</h1>
        <FilterBar preset={preset} onPreset={setPreset} />
      </div>

      <Tabs value={groupBy} onValueChange={(v) => setGroupBy(v as GroupBy)}>
        <TabsList>
          {VIEWS.map((v) => (
            <TabsTrigger key={v.value} value={v.value}>
              {v.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {(profit.error || saveError) && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          {saveError ?? `No se pudo cargar: ${profit.error}`}
        </div>
      )}
      {groupBy === 'product' && (
        <p className="text-muted-foreground text-sm">
          Edita el COGS unitario de cada producto; la utilidad se recalcula al salir del campo.
        </p>
      )}

      <Card>
        <CardContent className="overflow-x-auto pt-4">
          <ProfitTable
            rows={profit.data?.rows ?? []}
            totals={profit.data?.totals ?? null}
            groupBy={groupBy}
            loading={profit.loading}
            cogsBySku={groupBy === 'product' ? cogsBySku : undefined}
            onCogsChange={onCogsChange}
          />
        </CardContent>
      </Card>
    </div>
  )
}
