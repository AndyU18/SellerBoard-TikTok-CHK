// Límites de utilidad de cada rango del histograma de "Utilidad por orden" (claves fijas del backend:
// BINS en app/order_profit.py). Sirve para filtrar la tabla al hacer clic en una barra.
export interface ProfitLimits {
  min: number | null
  max: number | null
}

const BIN_LIMITS: Record<string, ProfitLimits> = {
  lt_m10: { min: null, max: -10 },
  m10_0: { min: -10, max: 0 },
  '0_5': { min: 0, max: 5 },
  '5_10': { min: 5, max: 10 },
  '10_20': { min: 10, max: 20 },
  gt_20: { min: 20, max: null },
}

/** Límites del rango `key`; null si la clave no existe (o está vacía). */
export function binLimits(key: string): ProfitLimits | null {
  return BIN_LIMITS[key] ?? null
}
