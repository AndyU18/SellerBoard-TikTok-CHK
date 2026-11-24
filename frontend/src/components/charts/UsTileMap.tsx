import { useState } from 'react'

import { blueScale } from './kit'

/** Casilla [columna, fila] de cada estado en un mapa de cuadrícula (cada estado del mismo tamaño). */
const TILES: Record<string, [number, number, string]> = {
  AK: [0, 0, 'Alaska'], ME: [10, 0, 'Maine'],
  VT: [9, 1, 'Vermont'], NH: [10, 1, 'New Hampshire'],
  WA: [0, 2, 'Washington'], ID: [1, 2, 'Idaho'], MT: [2, 2, 'Montana'], ND: [3, 2, 'North Dakota'], MN: [4, 2, 'Minnesota'],
  IL: [5, 2, 'Illinois'], WI: [6, 2, 'Wisconsin'], MI: [7, 2, 'Michigan'], NY: [8, 2, 'New York'], RI: [9, 2, 'Rhode Island'], MA: [10, 2, 'Massachusetts'],
  OR: [0, 3, 'Oregon'], NV: [1, 3, 'Nevada'], WY: [2, 3, 'Wyoming'], SD: [3, 3, 'South Dakota'], IA: [4, 3, 'Iowa'],
  IN: [5, 3, 'Indiana'], OH: [6, 3, 'Ohio'], PA: [7, 3, 'Pennsylvania'], NJ: [8, 3, 'New Jersey'], CT: [9, 3, 'Connecticut'],
  CA: [0, 4, 'California'], UT: [1, 4, 'Utah'], CO: [2, 4, 'Colorado'], NE: [3, 4, 'Nebraska'], MO: [4, 4, 'Missouri'],
  KY: [5, 4, 'Kentucky'], WV: [6, 4, 'West Virginia'], VA: [7, 4, 'Virginia'], MD: [8, 4, 'Maryland'], DE: [9, 4, 'Delaware'],
  AZ: [1, 5, 'Arizona'], NM: [2, 5, 'New Mexico'], KS: [3, 5, 'Kansas'], AR: [4, 5, 'Arkansas'], TN: [5, 5, 'Tennessee'],
  NC: [6, 5, 'North Carolina'], SC: [7, 5, 'South Carolina'], DC: [8, 5, 'District of Columbia'],
  OK: [3, 6, 'Oklahoma'], LA: [4, 6, 'Louisiana'], MS: [5, 6, 'Mississippi'], AL: [6, 6, 'Alabama'], GA: [7, 6, 'Georgia'],
  HI: [0, 7, 'Hawaii'], TX: [3, 7, 'Texas'], FL: [8, 7, 'Florida'],
}
const BY_NAME = Object.fromEntries(Object.entries(TILES).map(([abbr, [, , name]]) => [name.toLowerCase(), abbr]))

/** Acepta "Florida", "FL" o "florida" y devuelve la sigla, o null si no es un estado de EE. UU. */
export const stateCode = (s: string | null | undefined): string | null => {
  const t = (s ?? '').trim()
  if (TILES[t.toUpperCase()]) return t.toUpperCase()
  return BY_NAME[t.toLowerCase()] ?? null
}

const SIZE = 46

/**
 * Mapa de EE. UU. por estado (cuadrícula). `data` = [{ state, value }]; el color sube con el valor.
 * Pasar el cursor muestra el detalle (`describe`), un clic avisa con `onStateClick`.
 */
export function UsTileMap({
  data,
  format,
  describe,
  onStateClick,
  selected,
}: {
  data: { state: string; value: number; extra?: string }[]
  format: (v: number) => string
  describe?: (state: string, value: number | undefined, extra?: string) => string
  onStateClick?: (stateName: string) => void
  selected?: string | null
}) {
  const [hover, setHover] = useState<string | null>(null)
  const byCode = new Map<string, { value: number; extra?: string }>()
  let unmapped = 0
  for (const d of data) {
    const code = stateCode(d.state)
    if (code) byCode.set(code, { value: (byCode.get(code)?.value ?? 0) + d.value, extra: d.extra })
    else unmapped += 1
  }
  const max = Math.max(0, ...[...byCode.values()].map((v) => v.value))
  const hovered = hover ? { name: TILES[hover][2], ...byCode.get(hover) } : null

  return (
    <div>
      <svg viewBox={`0 0 ${11 * SIZE} ${8 * SIZE}`} className="w-full max-w-3xl" role="img" aria-label="Mapa de Estados Unidos por estado">
        {Object.entries(TILES).map(([abbr, [c, r, name]]) => {
          const v = byCode.get(abbr)
          return (
            <g
              key={abbr}
              transform={`translate(${c * SIZE},${r * SIZE})`}
              onMouseEnter={() => setHover(abbr)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onStateClick?.(name)}
              cursor={onStateClick ? 'pointer' : 'default'}
            >
              <rect
                width={SIZE - 3}
                height={SIZE - 3}
                rx={4}
                fill={v ? blueScale(max ? 0.15 + 0.85 * (v.value / max) : 0.15) : '#f1f5f9'}
                stroke={selected === name || hover === abbr ? '#0f172a' : 'transparent'}
                strokeWidth={2}
              />
              <text x={(SIZE - 3) / 2} y={(SIZE - 3) / 2 + 4} textAnchor="middle" fontSize={11} fontWeight={600} fill={v && max && v.value / max > 0.55 ? '#fff' : '#334155'}>
                {abbr}
              </text>
            </g>
          )
        })}
      </svg>
      <div className="text-muted-foreground mt-1 min-h-5 text-xs">
        {hovered
          ? describe
            ? describe(hovered.name, hovered.value, hovered.extra)
            : `${hovered.name}: ${hovered.value == null ? 'sin datos' : format(hovered.value)}`
          : 'Pasa el cursor sobre un estado para ver el detalle.'}
        {unmapped > 0 && <span className="ml-2">({unmapped} sin estado reconocido)</span>}
      </div>
    </div>
  )
}
