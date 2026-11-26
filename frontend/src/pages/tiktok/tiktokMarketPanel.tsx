// Panel lateral de un creador del mercado: ficha pública, puntaje explicado y anti-recomendación.
import { Callout, Hint, pct1 } from '@/components/analytics/ui'
import { count, money } from '@/lib/format'
import type { MarketCreator } from '@/lib/tiktokCreatorMarket'

import { Fact, SidePanel } from './tiktokCatalogUi'
import { ENGAGEMENT_NOTE, TAB_SAMPLE_POLICY, categoriesText, displayName, engagementText, gmvText, handleText, isNoSamples, isWorkingWith, partLabel, sizeLabel, unitsRangeText } from './tiktokMarketFormat'
import { CreatorAvatar, MarketBadges, MarketScoreBar, PartBars, SellerCenterLink } from './tiktokMarketUi'
import { NO_VALUE, shortDate } from './tiktokFormat'

function AntiRecommendation({ c, onOpenTab }: { c: MarketCreator; onOpenTab?: (tab: string) => void }) {
  if (!isNoSamples(c) && !isWorkingWith(c)) return null
  return (
    <section aria-label="Relación con nuestras muestras" className="space-y-2">
      <h3 className="text-sm font-semibold">{c.recommendation}</h3>
      <Callout tone={isNoSamples(c) ? 'danger' : 'info'}>{c.relation_reason ?? c.recommendation}</Callout>
      {onOpenTab && (
        <button type="button" onClick={() => onOpenTab(TAB_SAMPLE_POLICY)} className="text-xs underline hover:no-underline focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none">
          Ver la política de muestras
        </button>
      )}
    </section>
  )
}

function ScoreExplanation({ c }: { c: MarketCreator }) {
  return (
    <section aria-label="Cómo se calculó el puntaje" className="space-y-2">
      <h3 className="text-sm font-semibold">Cómo se calculó el puntaje</h3>
      <PartBars parts={c.score_parts} />
      <ul className="space-y-2 text-sm">
        {c.score_parts.map((p) => (
          <li key={p.name} className="rounded-md border p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{partLabel(p.name)}</span>
              <span className="tabular-nums">
                {p.points.toFixed(1)} de {p.max.toFixed(0)}
              </span>
            </div>
            <p className="text-muted-foreground mt-1 text-xs">{p.reason}</p>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function MarketCreatorPanel({ c, onClose, onOpenTab }: { c: MarketCreator; onClose: () => void; onOpenTab?: (tab: string) => void }) {
  const growth = c.follower_growth
  return (
    <SidePanel title={displayName(c)} subtitle={handleText(c)} onClose={onClose}>
      <div className="flex items-center gap-3">
        <CreatorAvatar c={c} className="size-20" />
        <MarketBadges c={c} />
      </div>
      <div>
        <p className="text-muted-foreground mb-1 text-xs">Puntaje de candidato</p>
        <MarketScoreBar score={c.score} />
      </div>
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <Fact label="Seguidores" value={count(c.followers)} />
        <Fact label="Tamaño" value={sizeLabel(c.size_tier)} />
        <Fact label="Categorías" value={categoriesText(c)} />
        <Fact label="Región" value={c.region ?? NO_VALUE} />
        <Fact label="GMV (rango de TikTok)" value={gmvText(c)} />
        <Fact label="Unidades vendidas (rango)" value={unitsRangeText(c)} />
        <Fact label="Engagement*" value={c.has_detail ? engagementText(c) : 'Ficha sin leer'} />
        <Fact label="GPM" value={c.gpm == null ? NO_VALUE : money(c.gpm)} />
        {c.brand_collaboration_count != null && <Fact label="Colaboraciones con marcas" value={count(c.brand_collaboration_count)} />}
        {growth != null && <Fact label="Crecimiento de seguidores" value={`${growth > 0 ? '+' : ''}${pct1(growth)}`} />}
        <Fact label="Visto por primera vez" value={shortDate(c.first_seen_at)} />
        <Fact label="Última vez visto" value={shortDate(c.last_seen_at)} />
      </dl>
      <Hint>{ENGAGEMENT_NOTE}</Hint>
      <AntiRecommendation c={c} onOpenTab={onOpenTab} />
      <ScoreExplanation c={c} />
      <SellerCenterLink />
      <p className="text-muted-foreground text-xs">Invitar al creador o enviarle una muestra se hace en el Seller Center; esta app no lo hace.</p>
    </SidePanel>
  )
}
