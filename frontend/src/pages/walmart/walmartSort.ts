// Orden de tablas de Walmart: estado de orden y comparación (null siempre al final).
export type SortDir = 1 | -1
export interface SortState<K extends string> {
  key: K
  dir: SortDir
}

/** Alterna el sentido si se repite la columna; si es otra, empieza descendente (ascendente en textos). */
export function nextSort<K extends string>(cur: SortState<K>, key: K, textKeys: readonly K[] = []): SortState<K> {
  if (cur.key === key) return { key, dir: cur.dir === 1 ? -1 : 1 }
  return { key, dir: textKeys.includes(key) ? 1 : -1 }
}

/** Compara dos valores de columna; los vacíos (null) siempre van al final. */
export function compareValues(a: string | number | null | undefined, b: string | number | null | undefined, dir: SortDir): number {
  if (a == null && b == null) return 0
  if (a == null) return 1
  if (b == null) return -1
  if (typeof a === 'string' && typeof b === 'string') return a.localeCompare(b) * dir
  return (Number(a) - Number(b)) * dir
}
