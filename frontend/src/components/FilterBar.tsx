import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { CHANNEL_LABEL, CHANNELS, type Channel } from '@/lib/api'
import { PRESETS, type Preset } from '@/lib/format'

export type ChannelFilter = Channel | 'all'

const CHANNEL_ITEMS = [
  { value: 'all', label: 'Todos los canales' },
  ...CHANNELS.map((c) => ({ value: c, label: CHANNEL_LABEL[c] })),
]

interface Props {
  preset: Preset
  onPreset: (p: Preset) => void
  channel: ChannelFilter
  onChannel: (c: ChannelFilter) => void
}

export function FilterBar({ preset, onPreset, channel, onChannel }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Select items={PRESETS} value={preset} onValueChange={(v) => v && onPreset(v as Preset)}>
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
      <Select
        items={CHANNEL_ITEMS}
        value={channel}
        onValueChange={(v) => v && onChannel(v as ChannelFilter)}
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
    </div>
  )
}
