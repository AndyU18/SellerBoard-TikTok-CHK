import { EmptyChart, PALETTE } from './kit'

export type FunnelStage = { id: string; label: string; value: number }

/**
 * Embudo horizontal. Cada barra mide su valor contra la primera etapa y entre etapas se muestra
 * el porcentaje que pasa a la siguiente. Clic en una etapa avisa con `onStageClick`.
 */
export function FunnelBars({
  stages,
  format = (v) => v.toLocaleString('en-US'),
  onStageClick,
}: {
  stages: FunnelStage[]
  format?: (v: number) => string
  onStageClick?: (stage: FunnelStage) => void
}) {
  if (!stages.length || !stages[0].value) return <EmptyChart />
  const top = stages[0].value
  return (
    <div className="space-y-1">
      {stages.map((s, i) => {
        const prev = i > 0 ? stages[i - 1].value : null
        return (
          <div key={s.id}>
            {prev != null && (
              <div className="text-muted-foreground pl-1 text-[11px]">
                ↓ {prev ? ((s.value / prev) * 100).toFixed(1) : '—'}% pasa a la siguiente etapa
              </div>
            )}
            <button
              type="button"
              onClick={() => onStageClick?.(s)}
              className="relative flex h-9 w-full items-center rounded bg-slate-100 text-left text-sm hover:bg-slate-200"
              title={`${s.label}: ${format(s.value)}`}
            >
              <span className="absolute inset-y-0 left-0 rounded" style={{ width: `${Math.max(2, (s.value / top) * 100)}%`, background: PALETTE[i % PALETTE.length], opacity: 0.85 }} />
              <span className="relative flex w-full justify-between px-3 font-medium">
                <span className="text-slate-900 mix-blend-normal">{s.label}</span>
                <span className="tabular-nums">{format(s.value)}</span>
              </span>
            </button>
          </div>
        )
      })}
    </div>
  )
}
