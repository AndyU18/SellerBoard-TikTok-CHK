import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { CHANNEL_LABEL, CHANNELS, type Channel } from '@/lib/api'
import { PRESETS, type Preset } from '@/lib/format'
import { useScope, type ChannelFilter } from '@/lib/scope'

const PERIOD_ITEMS = PRESETS.map((p) => ({ value: p.value, label: p.label }))

const CHANNEL_ITEMS = [
  { value: 'all', label: 'Todos los canales' },
  ...CHANNELS.map((c) => ({ value: c, label: CHANNEL_LABEL[c] })),
]

/** Barra global: canal y tienda que aplican a TODAS las pantallas. */
export function ScopeBar() {
  const scope = useScope()
  const stores = scope.accounts.filter((a) => scope.channel === 'all' || a.channel === scope.channel)
  const storeItems = [
    { value: '__all__', label: 'Todas las tiendas' },
    ...stores.map((a) => ({
      value: a.name,
      label: scope.channel === 'all' ? `${CHANNEL_LABEL[a.channel]} · ${a.name}` : a.name,
    })),
  ]

  return (
    <div className="flex flex-wrap items-center gap-3 border-b bg-white px-6 py-2 text-sm dark:bg-slate-900">
      <span className="text-muted-foreground text-xs font-medium uppercase">Vista</span>
      <Select
        items={CHANNEL_ITEMS}
        value={scope.channel}
        onValueChange={(v) => v && scope.setChannel(v as ChannelFilter)}
      >
        <SelectTrigger className="w-48" aria-label="Canal">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {CHANNEL_ITEMS.map((c) => (
            <SelectItem key={c.value} value={c.value}>
              {c.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        items={storeItems}
        value={scope.account || '__all__'}
        onValueChange={(v) => v && scope.setAccount(v === '__all__' ? '' : v)}
      >
        <SelectTrigger className="w-60" aria-label="Tienda">
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

/** Aviso para pantallas cuyos datos solo existen en ciertos canales (hoy: TikTok Shop). */
export function ChannelOnlyNotice({ channels, what }: { channels: Channel[]; what: string }) {
  const scope = useScope()
  if (scope.channel === 'all' || channels.includes(scope.channel)) return null
  return (
    <div role="status" className="rounded-md border border-slate-300 bg-slate-50 p-3 text-sm text-slate-800">
      <strong>
        {what} solo tiene datos de {channels.map((c) => CHANNEL_LABEL[c]).join(', ')}.
      </strong>{' '}
      Para {CHANNEL_LABEL[scope.channel]} no hay información todavía; cambia a “Todos los canales” o a ese canal para
      verla.
    </div>
  )
}
