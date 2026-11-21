import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { PRESETS, type Preset } from '@/lib/format'

/** Selector de periodo. El canal y la tienda se eligen en la barra global (ScopeBar). */
interface Props {
  preset: Preset
  onPreset: (p: Preset) => void
}

export function FilterBar({ preset, onPreset }: Props) {
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
    </div>
  )
}
