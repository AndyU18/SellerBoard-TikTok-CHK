// Panel lateral de una oportunidad de TikTok Creator: puntaje explicado, relación con nuestro catálogo y enlace.
import { Callout } from '@/components/analytics/ui'
import { count, money } from '@/lib/format'
import type { CreatorOpportunity, EquivalentProduct, ScoreConstants } from '@/lib/tiktokCreatorCatalog'

import { Fact, ProductImage, SidePanel } from './tiktokCatalogUi'
import { affinityGroupLabel, affinityReason, perUnitText, priceText, rateText, safeHttpUrl, unitsText } from './tiktokCreatorFormat'
import { OpportunityBadges, ScoreBar } from './tiktokCreatorUi'
import { NO_VALUE, shortDate } from './tiktokFormat'

const FACTOR_DECIMALS = 2

type FactorLine = { name: string; factor: number; detail: string }

/** Una línea por factor del puntaje, con la cuenta hecha en lenguaje claro a partir de las constantes del backend. */
function factorLines(p: CreatorOpportunity, c: ScoreConstants): FactorLine[] {
  return [
    {
      name: 'Comisión',
      factor: p.commission_factor,
      detail: `Ganas ${perUnitText(p)} por unidad. Con ${money(c.commission_reference_usd)} o más por unidad el factor llega a su máximo (1).`,
    },
    {
      name: 'Demanda',
      factor: p.demand_factor,
      detail: `${unitsText(p)} unidades vendidas en una escala logarítmica: de 10 a 100 pesa lo mismo que de 1.000 a 10.000. Llega a 1 con ${count(c.demand_saturation_units)} unidades y nunca baja de ${c.demand_floor}, porque un producto sin ventas aún no es un mal producto.`,
    },
    {
      name: 'Disponibilidad',
      factor: p.availability,
      detail: p.has_inventory ? 'TikTok informa inventario: se puede comprar (1).' : 'Sin inventario no se puede promocionar: el puntaje queda en 0.',
    },
    { name: 'Afinidad', factor: p.affinity, detail: affinityReason(p.affinity, c) },
  ]
}

function ScoreBreakdown({ p, formula, constants }: { p: CreatorOpportunity; formula: string; constants: ScoreConstants }) {
  const lines = factorLines(p, constants)
  const keywords = p.affinity_groups.flatMap((g) => constants.affinity_keywords[g] ?? [])
  return (
    <section aria-label="Cómo se calculó el puntaje" className="space-y-2">
      <h3 className="text-sm font-semibold">Cómo se calculó el puntaje</h3>
      <p className="text-muted-foreground text-xs">Puntaje = {formula}</p>
      <ul className="space-y-2 text-sm">
        {lines.map((l) => (
          <li key={l.name} className="rounded-md border p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{l.name}</span>
              <span className="tabular-nums">× {l.factor.toFixed(FACTOR_DECIMALS)}</span>
            </div>
            <p className="text-muted-foreground mt-1 text-xs">{l.detail}</p>
          </li>
        ))}
      </ul>
      {p.affinity_groups.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer">Grupos de afinidad: {p.affinity_groups.map(affinityGroupLabel).join(', ')}</summary>
          <p className="text-muted-foreground mt-1">Palabras clave: {keywords.join(', ')}</p>
        </details>
      )}
      {p.suspect && <p className="text-xs text-red-700">Un producto con datos sospechosos puntúa 0 por regla.</p>}
    </section>
  )
}

function priceComparison(e: EquivalentProduct): string {
  const diff = e.price_difference_pct
  if (diff == null) return `Nuestro precio ${money(e.our_price)} · este producto ${money(e.their_price)}`
  const direction = diff > 0 ? 'más caro' : diff < 0 ? 'más barato' : 'igual de caro'
  return `Nuestro precio ${money(e.our_price)} · este producto ${money(e.their_price)} (${Math.abs(diff).toFixed(1)} % ${direction})`
}

function CatalogRelationBlock({ p }: { p: CreatorOpportunity }) {
  const { sells_same_category: sameCategory, our_products_in_category: ours, equivalent } = p.relation
  if (!sameCategory && !equivalent) return <Callout tone="info">No vendemos nada de esta categoría ni un producto equivalente.</Callout>
  return (
    <div className="space-y-2">
      {sameCategory && <Callout tone="info">Vendemos {count(ours)} producto(s) de la misma categoría.</Callout>}
      {equivalent && (
        <Callout tone="info">
          <p className="font-medium">Producto equivalente nuestro: {equivalent.title}</p>
          <p className="text-xs">{priceComparison(equivalent)}</p>
          <p className="text-xs">Parecido de títulos: {Math.round(equivalent.similarity * 100)} %</p>
        </Callout>
      )}
    </div>
  )
}

export function CreatorProductPanel({ p, formula, constants, onClose }: { p: CreatorOpportunity; formula: string; constants: ScoreConstants; onClose: () => void }) {
  const link = safeHttpUrl(p.detail_link)
  return (
    <SidePanel title={p.title ?? p.product_id} subtitle={`ID ${p.product_id}`} onClose={onClose}>
      <ProductImage src={p.image_url} title={p.title} className="aspect-square w-full" />
      <OpportunityBadges p={p} />
      {p.suspect && <Callout tone="danger">Este producto trae importes o unidades imposibles (datos de prueba de TikTok), por eso no se muestran.</Callout>}
      <div>
        <p className="text-muted-foreground mb-1 text-xs">Puntaje de oportunidad</p>
        <ScoreBar score={p.score} />
      </div>
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <Fact label="Comisión" value={rateText(p)} />
        <Fact label="Comisión por unidad (estimada)" value={perUnitText(p)} />
        <Fact label="Precio" value={priceText(p)} />
        <Fact label="Unidades vendidas" value={unitsText(p)} />
        <Fact label="Tienda" value={p.shop_name ?? NO_VALUE} />
        <Fact label="Categoría" value={p.category ?? NO_VALUE} />
        <Fact label="Región de venta" value={p.sale_region ?? NO_VALUE} />
        <Fact label="Visto por primera vez" value={shortDate(p.first_seen_at)} />
      </dl>
      <ScoreBreakdown p={p} formula={formula} constants={constants} />
      <section aria-label="Relación con nuestro catálogo" className="space-y-2">
        <h3 className="text-sm font-semibold">Relación con nuestro catálogo</h3>
        <CatalogRelationBlock p={p} />
      </section>
      {link ? (
        <a href={link} target="_blank" rel="noopener noreferrer" className="inline-block rounded-md border px-3 py-2 text-sm font-medium hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
          Ver producto en TikTok (se abre en otra pestaña)
        </a>
      ) : (
        <p className="text-muted-foreground text-xs">Este producto no trae enlace.</p>
      )}
    </SidePanel>
  )
}
