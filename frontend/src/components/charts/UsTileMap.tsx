import { geoPath } from 'd3-geo'
import type { FeatureCollection, Geometry } from 'geojson'
import { useMemo, useState } from 'react'
import { feature, mesh } from 'topojson-client'
import type { GeometryCollection, Topology } from 'topojson-specification'
import atlas from 'us-atlas/states-albers-10m.json'

import { blueScale } from './kit'

const ABBR: Record<string, string> = {
  Alabama: 'AL', Alaska: 'AK', Arizona: 'AZ', Arkansas: 'AR', California: 'CA', Colorado: 'CO', Connecticut: 'CT',
  Delaware: 'DE', 'District of Columbia': 'DC', Florida: 'FL', Georgia: 'GA', Hawaii: 'HI', Idaho: 'ID', Illinois: 'IL',
  Indiana: 'IN', Iowa: 'IA', Kansas: 'KS', Kentucky: 'KY', Louisiana: 'LA', Maine: 'ME', Maryland: 'MD',
  Massachusetts: 'MA', Michigan: 'MI', Minnesota: 'MN', Mississippi: 'MS', Missouri: 'MO', Montana: 'MT',
  Nebraska: 'NE', Nevada: 'NV', 'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
  'North Carolina': 'NC', 'North Dakota': 'ND', Ohio: 'OH', Oklahoma: 'OK', Oregon: 'OR', Pennsylvania: 'PA',
  'Rhode Island': 'RI', 'South Carolina': 'SC', 'South Dakota': 'SD', Tennessee: 'TN', Texas: 'TX', Utah: 'UT',
  Vermont: 'VT', Virginia: 'VA', Washington: 'WA', 'West Virginia': 'WV', Wisconsin: 'WI', Wyoming: 'WY',
}
const BY_NAME = Object.fromEntries(Object.entries(ABBR).map(([name, abbr]) => [name.toLowerCase(), abbr]))
const NAME_BY_ABBR = Object.fromEntries(Object.entries(ABBR).map(([name, abbr]) => [abbr, name]))

/** Acepta "Florida", "FL" o "florida" y devuelve la sigla, o null si no es un estado de EE. UU. */
export const stateCode = (s: string | null | undefined): string | null => {
  const t = (s ?? '').trim()
  if (NAME_BY_ABBR[t.toUpperCase()]) return t.toUpperCase()
  return BY_NAME[t.toLowerCase()] ?? null
}

// Mapa ya proyectado (Albers, con Alaska y Hawái en recuadro) de us-atlas: no hace falta proyectar nada.
const topo = atlas as unknown as Topology
const statesObject = topo.objects.states as GeometryCollection<{ name: string }>
const collection = feature(topo, statesObject) as FeatureCollection<Geometry, { name: string }>
const toPath = geoPath()
const SHAPES = collection.features.map((f) => {
  const [cx, cy] = toPath.centroid(f)
  return { name: f.properties.name, code: stateCode(f.properties.name), d: toPath(f) ?? '', cx, cy, area: toPath.area(f) }
})
const BORDERS = toPath(mesh(topo, statesObject, (a, b) => a !== b)) ?? ''
const LABELED_MIN_AREA = 2600 // los estados muy pequeños no llevan sigla encima (se ven al pasar el cursor)

/**
 * Mapa de EE. UU. por estado. `data` = [{ state, value }]; el color sube con el valor.
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
  const [hover, setHover] = useState<{ code: string; x: number; y: number } | null>(null)
  const { byCode, max, min, unmapped } = useMemo(() => {
    const map = new Map<string, { value: number; extra?: string }>()
    let skipped = 0
    for (const d of data) {
      const code = stateCode(d.state)
      if (code) map.set(code, { value: (map.get(code)?.value ?? 0) + d.value, extra: d.extra })
      else skipped += 1
    }
    const values = [...map.values()].map((v) => v.value)
    return { byCode: map, max: Math.max(0, ...values), min: values.length ? Math.min(...values) : 0, unmapped: skipped }
  }, [data])

  const top = [...byCode.entries()].sort((a, b) => b[1].value - a[1].value).slice(0, 6)
  const tone = (value: number) => (max > min ? 0.12 + 0.88 * ((value - min) / (max - min)) : 0.6)
  const hovered = hover ? { name: NAME_BY_ABBR[hover.code], ...byCode.get(hover.code) } : null
  const tip = hovered
    ? describe
      ? describe(hovered.name, hovered.value, hovered.extra)
      : `${hovered.name}: ${hovered.value == null ? 'sin datos' : format(hovered.value)}`
    : ''

  return (
    <div className="relative">
      <svg viewBox="0 0 975 610" className="h-auto w-full max-w-4xl" role="img" aria-label="Mapa de Estados Unidos por estado">
        <g>
          {SHAPES.map((s) => {
            const v = s.code ? byCode.get(s.code) : undefined
            const isSelected = !!selected && selected === s.name
            return (
              <path
                key={s.name}
                d={s.d}
                fill={v ? blueScale(tone(v.value)) : '#e8edf3'}
                stroke={isSelected ? '#0f172a' : 'transparent'}
                strokeWidth={isSelected ? 2.5 : 0}
                opacity={hover && hover.code !== s.code ? 0.78 : 1}
                cursor={onStateClick ? 'pointer' : 'default'}
                onMouseMove={(e) => {
                  if (!s.code) return
                  const box = e.currentTarget.ownerSVGElement?.getBoundingClientRect()
                  if (box) setHover({ code: s.code, x: e.clientX - box.left, y: e.clientY - box.top })
                }}
                onMouseLeave={() => setHover(null)}
                onClick={() => onStateClick?.(s.name)}
              >
                <title>{s.name}</title>
              </path>
            )
          })}
        </g>
        <path d={BORDERS} fill="none" stroke="#fff" strokeWidth={1} strokeLinejoin="round" pointerEvents="none" />
        {SHAPES.filter((s) => s.code && s.area > LABELED_MIN_AREA).map((s) => {
          const v = byCode.get(s.code!)
          const dark = v && tone(v.value) > 0.55
          return (
            <text key={s.name} x={s.cx} y={s.cy + 4} textAnchor="middle" fontSize={13} fontWeight={600} fill={dark ? '#fff' : '#334155'} pointerEvents="none">
              {s.code}
            </text>
          )
        })}
      </svg>

      {hover && hovered && (
        <div
          className="pointer-events-none absolute z-10 max-w-xs rounded-md border bg-white px-2 py-1 text-xs shadow-md dark:bg-slate-900"
          style={{ left: Math.min(hover.x + 12, 560), top: hover.y + 12 }}
        >
          {tip}
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">
        {max > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">{format(min)}</span>
            <span className="h-2.5 w-32 rounded" style={{ background: `linear-gradient(to right, ${blueScale(0.12)}, ${blueScale(1)})` }} />
            <span className="text-muted-foreground">{format(max)}</span>
          </div>
        )}
        {top.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-muted-foreground">Mayores:</span>
            {top.map(([code, v]) => (
              <button
                key={code}
                type="button"
                onClick={() => onStateClick?.(NAME_BY_ABBR[code])}
                onMouseEnter={() => setHover({ code, x: 0, y: 0 })}
                onMouseLeave={() => setHover(null)}
                className="rounded-full border px-2 py-0.5 hover:bg-slate-100"
              >
                {code} · {format(v.value)}
              </button>
            ))}
          </div>
        )}
        {unmapped > 0 && <span className="text-muted-foreground">({unmapped} sin estado reconocido)</span>}
        {!hover && <span className="text-muted-foreground">Pasa el cursor sobre un estado para ver el detalle.</span>}
      </div>
    </div>
  )
}
