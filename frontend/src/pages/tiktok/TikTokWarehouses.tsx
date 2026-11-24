import { useMemo } from 'react'

import { Callout, ErrorAlert, Kpi, Pill, PILL } from '@/components/analytics/ui'
import { ChartCard, EmptyChart } from '@/components/charts/kit'
import { UsTileMap } from '@/components/charts/UsTileMap'
import { useScopeDescription } from '@/components/PageHeader'
import { Card, CardContent } from '@/components/ui/card'
import { count } from '@/lib/format'
import { type WarehouseKind, type WarehouseRow, tiktokMoney, warehouseKind } from '@/lib/tiktokMoney'
import { useAsync } from '@/lib/useAsync'

import { PageTitle } from '../walmart/walmartUi'
import { StatusPill } from './tiktokMoneyUi'

const KIND_LABEL: Record<WarehouseKind, string> = { sales: 'Ventas', returns: 'Devoluciones', other: 'Otros' }
const KIND_PILL: Record<WarehouseKind, string> = { sales: PILL.blue, returns: PILL.orange, other: PILL.slate }
const KIND_HINT: Record<WarehouseKind, string> = {
  sales: 'desde donde salen los pedidos',
  returns: 'a donde llegan las devoluciones',
  other: 'otro tipo de almacén',
}
const PRIVACY_NOTE =
  'Solo se guardan la ciudad, el estado y el código postal de cada almacén: no se almacenan calles ni datos de contacto.'

const place = (w: WarehouseRow): string => [w.city, [w.state, w.postal_code].filter(Boolean).join(' '), w.region_code].filter(Boolean).join(', ') || 'Ubicación no disponible'

function WarehouseCard({ w }: { w: WarehouseRow }) {
  const kind = warehouseKind(w.type)
  return (
    <Card>
      <CardContent className="space-y-2 pt-4">
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-medium break-words">{w.name ?? w.warehouse_id}</h3>
          <StatusPill status={w.status} />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Pill label={KIND_LABEL[kind]} className={KIND_PILL[kind]} />
          {w.is_default && <Pill label="Predeterminado" className={PILL.green} />}
        </div>
        <p className="text-muted-foreground text-sm">{place(w)}</p>
        <p className="text-muted-foreground font-mono text-xs break-all">{w.warehouse_id}</p>
      </CardContent>
    </Card>
  )
}

export function TikTokWarehouses() {
  const description = useScopeDescription()
  const report = useAsync(() => tiktokMoney.warehouses(), [])
  const list = useMemo(() => report.data?.warehouses ?? [], [report.data])

  const byKind = useMemo(() => {
    const totals: Record<WarehouseKind, number> = { sales: 0, returns: 0, other: 0 }
    for (const w of list) totals[warehouseKind(w.type)] += 1
    return totals
  }, [list])
  const mapData = useMemo(() => {
    const perState = new Map<string, number>()
    for (const w of list) if (w.state) perState.set(w.state, (perState.get(w.state) ?? 0) + 1)
    return [...perState.entries()].map(([state, value]) => ({ state, value }))
  }, [list])

  const kinds = (Object.keys(KIND_LABEL) as WarehouseKind[]).filter((k) => k !== 'other' || byKind.other > 0)

  return (
    <div className="space-y-6">
      <PageTitle title="Almacenes" scope={description} note="Los almacenes registrados en tu tienda de TikTok Shop." />
      <ErrorAlert message={report.error} />
      <Callout tone="info">{PRIVACY_NOTE}</Callout>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi title="Almacenes" value={report.data ? count(list.length) : '—'} hint="en total" />
        {kinds.map((k) => (
          <Kpi key={k} title={KIND_LABEL[k]} value={report.data ? count(byKind[k]) : '—'} hint={KIND_HINT[k]} />
        ))}
      </div>

      <ChartCard title="Estados con almacenes" subtitle="Cuántos almacenes hay en cada estado de EE. UU." filename="tiktok-almacenes-estados" csv={mapData.map((d) => ({ Estado: d.state, Almacenes: d.value }))}>
        {mapData.length === 0 ? (
          <EmptyChart>{report.loading ? 'Cargando…' : 'Ningún almacén tiene un estado de EE. UU. registrado.'}</EmptyChart>
        ) : (
          <UsTileMap data={mapData} format={count} describe={(state, value) => `${state}: ${count(value ?? 0)} almacén(es)`} />
        )}
      </ChartCard>

      {list.length > 0 ? (
        <section aria-label="Listado de almacenes" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {list.map((w) => (
            <WarehouseCard key={w.warehouse_id} w={w} />
          ))}
        </section>
      ) : (
        <Card>
          <CardContent className="text-muted-foreground py-8 text-center text-sm">{report.loading ? 'Cargando…' : 'TikTok Shop aún no ha sincronizado almacenes.'}</CardContent>
        </Card>
      )}
    </div>
  )
}
