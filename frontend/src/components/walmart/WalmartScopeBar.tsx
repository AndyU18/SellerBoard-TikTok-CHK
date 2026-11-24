import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { CHANNEL_COLOR } from '@/lib/api'
import { PRESETS, type Preset } from '@/lib/format'
import { useScope } from '@/lib/scope'

const PERIOD_ITEMS = PRESETS.map((p) => ({ value: p.value, label: p.label }))

/**
 * Barra de filtros del espacio Walmart: el canal está fijo (se ve, pero no se puede cambiar) y solo se elige
 * la tienda de Walmart y el período. Debe usarse dentro de `ForcedChannel`.
 */
export function WalmartScopeBar() {
  const scope = useScope()
  const storeItems = [{ value: '__all__', label: 'Todas las tiendas de Walmart' }, ...scope.accounts.map((a) => ({ value: a.name, label: a.name }))]

  return (
    <div className="flex flex-wrap items-center gap-3 border-b bg-white px-6 py-2 text-sm dark:bg-slate-900">
      <span className="text-muted-foreground text-xs font-medium uppercase">Vista</span>
      <span
        className="inline-flex items-center gap-2 rounded-md border px-3 py-1.5 font-medium"
        title="En este espacio todo se ve filtrado a Walmart"
        style={{ borderColor: CHANNEL_COLOR.walmart, color: CHANNEL_COLOR.walmart }}
      >
        <span className="inline-block size-2 rounded-full" style={{ background: CHANNEL_COLOR.walmart }} />
        Walmart
      </span>
      {scope.accounts.length > 0 && (
        <Select items={storeItems} value={scope.account || '__all__'} onValueChange={(v) => v && scope.setAccount(v === '__all__' ? '' : v)}>
          <SelectTrigger className="w-60" aria-label="Tienda de Walmart">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {storeItems.map((s) => (
              <SelectItem key={s.value} value={s.value}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <span className="text-muted-foreground ml-2 text-xs font-medium uppercase">Período</span>
      <Select items={PERIOD_ITEMS} value={scope.preset} onValueChange={(v) => v && scope.setPreset(v as Preset)}>
        <SelectTrigger className="w-44" aria-label="Período">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {PERIOD_ITEMS.map((p) => (
            <SelectItem key={p.value} value={p.value}>
              {p.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
