// Simulador "¿y si...?": tipos, llamada a la API (backend: app/simulator_api.py) y el cálculo puro `simulate`.
// Importado por pages/Simulator. Todo el recálculo ocurre en el navegador sobre la base ya descargada.
import { authFetch, parse, query, type Channel } from '@/lib/api'

export interface SimRow {
  sku: string
  channel: Channel
  product_name: string | null
  has_cost: boolean
  units: number
  units_per_day: number
  gross_sales: number
  promo: number
  platform_fees: number
  shipping: number
  refunds: number
  cogs: number
  net_sales: number
  avg_price: number | null
  promo_per_unit: number | null
  fee_per_unit: number | null
  fee_pct_of_net: number | null
  shipping_per_unit: number | null
  refund_per_unit: number | null
  refund_rate: number | null
  cogs_per_unit: number | null
  profit: number
  profit_per_unit: number | null
  monthly_profit: number
  ad_spend: number
  ad_per_unit: number | null
  monthly_ad_spend: number
  profit_after_ads: number
  monthly_profit_after_ads: number
}

export interface SimBaselineResponse {
  days: number
  start: string
  end: string
  month_days: number
  skus: number
  skus_without_cost: number
  rows: SimRow[]
  totals: SimRow
}

export function fetchSimulatorBaseline(p: { days: number; channel?: string; account?: string }) {
  return authFetch(
    `/api/simulator/baseline${query({ days: String(p.days), channel: p.channel || undefined, account: p.account || undefined })}`,
  ).then((r) => parse<SimBaselineResponse>(r))
}

/** Economía unitaria base (lo que pasó) de lo seleccionado: un SKU, varios o todo el canal. */
export interface Baseline {
  days: number
  monthDays: number
  units: number
  unitsPerMonth: number
  price: number
  promo: number
  /** Comisión como fracción de la venta neta (0.15 = 15%). */
  feeRate: number
  shipping: number
  /** Reembolsos como fracción de la venta neta. */
  refundRate: number
  cogs: number
  /** Publicidad asignada, por mes (se muestra aparte de la utilidad). */
  adMonthly: number
  hasCost: boolean
}

const MONTH_DAYS = 30

/** Suma varias filas (SKU x canal) en una sola base ponderada por unidades. Null si no hubo ventas. */
export function aggregateBaseline(rows: SimRow[], days: number): Baseline | null {
  const units = rows.reduce((a, r) => a + r.units, 0)
  if (units <= 0) return null
  const sum = (f: (r: SimRow) => number) => rows.reduce((a, r) => a + f(r), 0)
  const gross = sum((r) => r.gross_sales)
  const promo = sum((r) => r.promo)
  const net = gross - promo
  return {
    days,
    monthDays: MONTH_DAYS,
    units,
    unitsPerMonth: (units / days) * MONTH_DAYS,
    price: gross / units,
    promo: promo / units,
    feeRate: net > 0 ? sum((r) => r.platform_fees) / net : 0,
    shipping: sum((r) => r.shipping) / units,
    refundRate: net > 0 ? sum((r) => r.refunds) / net : 0,
    cogs: sum((r) => r.cogs) / units,
    adMonthly: (sum((r) => r.ad_spend) / days) * MONTH_DAYS,
    hasCost: rows.every((r) => r.has_cost),
  }
}

/** Cambios del escenario. Todo en porcentaje (10 = +10%) salvo `commissionPts` (puntos) y `elasticity`. */
export interface Changes {
  pricePct: number
  costPct: number
  promoPct: number
  commissionPts: number
  shippingPct: number
  unitsPct: number
  /** Si el precio sube X%, las unidades bajan elasticidad x X%. */
  elasticity: number
  includeAds: boolean
}

export const NO_CHANGES: Changes = {
  pricePct: 0,
  costPct: 0,
  promoPct: 0,
  commissionPts: 0,
  shippingPct: 0,
  unitsPct: 0,
  elasticity: 0,
  includeAds: false,
}

export interface Outcome {
  price: number
  promo: number
  net: number
  fee: number
  shipping: number
  refund: number
  cogs: number
  profitPerUnit: number
  unitsPerMonth: number
  /** Utilidad mensual (menos la publicidad si `includeAds`). */
  monthlyProfit: number
  /** Utilidad por unidad / venta neta por unidad. */
  margin: number | null
  /** Utilidad por unidad / costo por unidad. */
  roi: number | null
  /** Precio de lista con el que la utilidad por unidad es 0 (null si ninguna venta alcanza). */
  breakEvenPrice: number | null
}

const pos = (n: number) => Math.max(0, n)

/** Factor de unidades por el cambio de precio: sube X% el precio, bajan elasticidad x X% las unidades. */
const priceVolume = (pricePct: number, elasticity: number) => pos(1 - (elasticity * pricePct) / 100)

/** Calcula la utilidad por unidad y mensual con los cambios aplicados. Función pura. */
export function simulate(b: Baseline, c: Changes): Outcome {
  const price = b.price * (1 + c.pricePct / 100)
  const promo = pos(b.promo * (1 + c.promoPct / 100))
  const net = price - promo
  const feeRate = pos(b.feeRate + c.commissionPts / 100)
  const fee = feeRate * net
  const shipping = pos(b.shipping * (1 + c.shippingPct / 100))
  const refund = b.refundRate * net
  const cogs = pos(b.cogs * (1 + c.costPct / 100))
  const profitPerUnit = net - fee - shipping - refund - cogs
  const unitsPerMonth = pos(b.unitsPerMonth * (1 + c.unitsPct / 100) * priceVolume(c.pricePct, c.elasticity))
  const monthlyProfit = profitPerUnit * unitsPerMonth - (c.includeAds ? b.adMonthly : 0)
  const keep = 1 - feeRate - b.refundRate
  return {
    price,
    promo,
    net,
    fee,
    shipping,
    refund,
    cogs,
    profitPerUnit,
    unitsPerMonth,
    monthlyProfit,
    margin: net > 0 ? profitPerUnit / net : null,
    roi: cogs > 0 ? profitPerUnit / cogs : null,
    breakEvenPrice: keep > 0 ? (shipping + cogs) / keep + promo : null,
  }
}

export interface CurvePoint {
  pricePct: number
  price: number
  monthlyProfit: number
}

export interface Curve {
  points: CurvePoint[]
  /** Mejor precio dentro del rango explorado. `edge` = cae en un extremo: no hay un óptimo real. */
  best: CurvePoint
  edge: boolean
}

/** Utilidad mensual contra precio (los demás cambios fijos). Rango: -40% a +80% del precio base. */
export function profitCurve(b: Baseline, c: Changes, from = -40, to = 80, step = 2): Curve {
  const points: CurvePoint[] = []
  for (let p = from; p <= to + 1e-9; p += step) {
    const o = simulate(b, { ...c, pricePct: p })
    points.push({ pricePct: p, price: o.price, monthlyProfit: o.monthlyProfit })
  }
  const best = points.reduce((a, x) => (x.monthlyProfit > a.monthlyProfit ? x : a), points[0])
  return { points, best, edge: best.pricePct === from || best.pricePct === to }
}

export type VariableKey = 'price' | 'cost' | 'promo' | 'commission' | 'shipping' | 'units'

export const VARIABLE_LABEL: Record<VariableKey, string> = {
  price: 'Precio',
  cost: 'Costo (COGS)',
  promo: 'Promoción',
  commission: 'Comisión',
  shipping: 'Envío',
  units: 'Unidades',
}

/** Aplica un cambio relativo de `pct`% a una variable, sumado a lo que ya tenga el escenario. */
export function nudge(b: Baseline, c: Changes, key: VariableKey, pct: number): Changes {
  switch (key) {
    case 'price':
      return { ...c, pricePct: c.pricePct + pct }
    case 'cost':
      return { ...c, costPct: c.costPct + pct }
    case 'promo':
      return { ...c, promoPct: c.promoPct + pct }
    case 'shipping':
      return { ...c, shippingPct: c.shippingPct + pct }
    case 'units':
      return { ...c, unitsPct: c.unitsPct + pct }
    case 'commission':
      // +10% de la comisión actual, expresado en puntos.
      return { ...c, commissionPts: c.commissionPts + (b.feeRate * 100 * pct) / 100 }
  }
}

export interface TornadoBar {
  key: VariableKey
  label: string
  /** Cambio de la utilidad mensual con -10% y +10% de la variable, frente al escenario actual. */
  down: number
  up: number
}

/** Cuánto mueve la utilidad mensual un -10% / +10% de cada variable (alrededor del escenario actual). */
export function sensitivity(b: Baseline, c: Changes, pct = 10): TornadoBar[] {
  const base = simulate(b, c).monthlyProfit
  const keys = Object.keys(VARIABLE_LABEL) as VariableKey[]
  return keys
    .map((key) => ({
      key,
      label: VARIABLE_LABEL[key],
      down: simulate(b, nudge(b, c, key, -pct)).monthlyProfit - base,
      up: simulate(b, nudge(b, c, key, pct)).monthlyProfit - base,
    }))
    .sort((x, y) => Math.max(Math.abs(y.down), Math.abs(y.up)) - Math.max(Math.abs(x.down), Math.abs(x.up)))
}

export interface DeltaStep {
  id: string
  label: string
  value: number
}

/** Cambio de la utilidad mensual por concepto: se aplican uno a uno y cada paso suma lo que aportó. */
export function monthlyDeltas(b: Baseline, c: Changes): DeltaStep[] {
  const order: { id: string; label: string; apply: (acc: Changes) => Changes }[] = [
    { id: 'price', label: 'Precio', apply: (a) => ({ ...a, pricePct: c.pricePct, elasticity: c.elasticity }) },
    { id: 'promo', label: 'Promoción', apply: (a) => ({ ...a, promoPct: c.promoPct }) },
    { id: 'commission', label: 'Comisión', apply: (a) => ({ ...a, commissionPts: c.commissionPts }) },
    { id: 'shipping', label: 'Envío', apply: (a) => ({ ...a, shippingPct: c.shippingPct }) },
    { id: 'cost', label: 'Costo', apply: (a) => ({ ...a, costPct: c.costPct }) },
    { id: 'units', label: 'Unidades', apply: (a) => ({ ...a, unitsPct: c.unitsPct }) },
  ]
  let acc: Changes = { ...NO_CHANGES, includeAds: c.includeAds }
  let prev = simulate(b, acc).monthlyProfit
  const out: DeltaStep[] = []
  for (const s of order) {
    acc = s.apply(acc)
    const now = simulate(b, acc).monthlyProfit
    out.push({ id: s.id, label: s.label, value: now - prev })
    prev = now
  }
  return out
}

export interface Lever {
  key: string
  label: string
  gain: number
}

/** Mejores palancas de una base: cuánto ganaría al mes con un ajuste moderado de cada una. */
export function leverGains(b: Baseline, elasticity: number, includeAds = false): Lever[] {
  const base: Changes = { ...NO_CHANGES, elasticity, includeAds }
  const start = simulate(b, base).monthlyProfit
  const tries: { key: string; label: string; c: Changes }[] = [
    { key: 'price', label: 'Precio +5%', c: { ...base, pricePct: 5 } },
    { key: 'cost', label: 'Costo -10%', c: { ...base, costPct: -10 } },
    { key: 'promo', label: 'Promo -25%', c: { ...base, promoPct: -25 } },
    { key: 'commission', label: 'Comisión -1 pt', c: { ...base, commissionPts: -1 } },
    { key: 'shipping', label: 'Envío -10%', c: { ...base, shippingPct: -10 } },
    { key: 'units', label: 'Unidades +10%', c: { ...base, unitsPct: 10 } },
  ]
  return tries.map((t) => ({ key: t.key, label: t.label, gain: simulate(b, t.c).monthlyProfit - start }))
}

export interface SavedScenario {
  id: string
  name: string
  /** Qué estaba seleccionado cuando se guardó (para el nombre en pantalla). */
  target: string
  changes: Changes
  savedAt: string
}

const STORAGE_KEY = 'chk.simulator.scenarios.v1'

export function loadScenarios(): SavedScenario[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((s): s is SavedScenario => !!s && typeof s.id === 'string' && typeof s.name === 'string' && !!s.changes)
      .map((s) => ({ ...s, changes: { ...NO_CHANGES, ...s.changes } }))
  } catch {
    return []
  }
}

export function storeScenarios(list: SavedScenario[]): boolean {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list))
    return true
  } catch {
    return false
  }
}
