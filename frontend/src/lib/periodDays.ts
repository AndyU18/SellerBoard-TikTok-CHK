/** Días que abarca el período global (para los análisis que piden una ventana en días). Sin inicio = 365. */
export function rangeDays(range: { start?: string; end?: string }, min = 7, max = 365): number {
  if (!range.start || !range.end) return max
  const ms = new Date(`${range.end}T00:00:00`).getTime() - new Date(`${range.start}T00:00:00`).getTime()
  const days = Math.round(ms / 86_400_000) + 1
  return Math.max(min, Math.min(max, Number.isFinite(days) ? days : max))
}
