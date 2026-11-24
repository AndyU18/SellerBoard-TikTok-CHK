import type { ComponentType } from 'react'

import { ChannelScopeBar } from '@/components/ChannelScopeBar'
import { PageHeader } from '@/components/PageHeader'
import { TIKTOK_MENU, type TikTokTab } from '@/components/tiktok/tiktokMenu'
import { ForcedChannel } from '@/lib/scope'
import { CreatorProfit } from '@/pages/CreatorProfit'
import { Creatives } from '@/pages/Creatives'
import { Funnel } from '@/pages/Funnel'
import { Inventory } from '@/pages/Inventory'
import { Live } from '@/pages/Live'
import { Reconciliation } from '@/pages/Reconciliation'
import { Returns } from '@/pages/Returns'
import { SampleStrategy } from '@/pages/SampleStrategy'
import { Samples } from '@/pages/Samples'
import { SettlementAging } from '@/pages/SettlementAging'
import { SettlementBreakdown } from '@/pages/SettlementBreakdown'
import { TikTokCatalog } from '@/pages/tiktok/TikTokCatalog'
import { TikTokCoupons } from '@/pages/tiktok/TikTokCoupons'
import { TikTokOrders } from '@/pages/tiktok/TikTokOrders'
import { TikTokOrganicConnection } from '@/pages/tiktok/TikTokOrganicConnection'
import { TikTokOrganicHashtags } from '@/pages/tiktok/TikTokOrganicHashtags'
import { TikTokOrganicOverview } from '@/pages/tiktok/TikTokOrganicOverview'
import { TikTokOrganicPerformance } from '@/pages/tiktok/TikTokOrganicPerformance'
import { TikTokOrganicTimes } from '@/pages/tiktok/TikTokOrganicTimes'
import { TikTokOrganicVideos } from '@/pages/tiktok/TikTokOrganicVideos'
import { TikTokOrganicVsShop } from '@/pages/tiktok/TikTokOrganicVsShop'
import { TikTokOverview } from '@/pages/tiktok/TikTokOverview'
import { TikTokPackages } from '@/pages/tiktok/TikTokPackages'
import { TikTokPayments } from '@/pages/tiktok/TikTokPayments'
import { TikTokProductPerformance } from '@/pages/tiktok/TikTokProductPerformance'
import { TikTokPromotions } from '@/pages/tiktok/TikTokPromotions'
import { TikTokSkuPerformance } from '@/pages/tiktok/TikTokSkuPerformance'
import { TikTokWarehouses } from '@/pages/tiktok/TikTokWarehouses'
import { TikTokWebhooks } from '@/pages/tiktok/TikTokWebhooks'
import { TikTokWithdrawals } from '@/pages/tiktok/TikTokWithdrawals'

type Screen = ComponentType<{ onOpenTab?: (tab: string) => void; onNavigate?: (page: string) => void }>

/** Cada sección del menú desplegable abre una pantalla; las de TikTok pueden saltar a otra sección con `onOpenTab`. */
const SCREENS: Record<TikTokTab, Screen> = {
  overview: TikTokOverview,
  orders: TikTokOrders,
  returns: Returns,
  inventory: Inventory,
  packages: TikTokPackages,
  warehouses: TikTokWarehouses,
  productPerformance: TikTokProductPerformance,
  skuPerformance: TikTokSkuPerformance,
  catalog: TikTokCatalog,
  promotions: TikTokPromotions,
  coupons: TikTokCoupons,
  performance: Funnel,
  live: Live,
  creatives: Creatives,
  samples: Samples,
  sampleStrategy: SampleStrategy,
  creatorProfit: CreatorProfit,
  settlements: SettlementBreakdown,
  settlementAging: SettlementAging,
  reconciliation: Reconciliation,
  payments: TikTokPayments,
  withdrawals: TikTokWithdrawals,
  webhooks: TikTokWebhooks,
  organicOverview: TikTokOrganicOverview,
  organicVideos: TikTokOrganicVideos,
  organicPerformance: TikTokOrganicPerformance,
  organicTimes: TikTokOrganicTimes,
  organicHashtags: TikTokOrganicHashtags,
  organicVsShop: TikTokOrganicVsShop,
  organicConnection: TikTokOrganicConnection,
}

/**
 * Espacio "TikTok": un solo apartado del menú lateral con lista desplegable. Todo lo que se muestra aquí está
 * filtrado a TikTok Shop (`ForcedChannel`), también las pantallas compartidas con los demás canales. La sección
 * activa la elige el menú lateral (`tab`); aquí solo se dibuja.
 */
export function TikTokHub({ tab, onTab }: { tab: TikTokTab; onTab: (tab: TikTokTab) => void }) {
  const current = TIKTOK_MENU.find((t) => t.id === tab) ?? TIKTOK_MENU[0]
  const Content = SCREENS[current.id]
  const organic = current.group === 'TikTok orgánico'

  return (
    <ForcedChannel channel="tiktok_shop">
      <ChannelScopeBar channel="tiktok_shop" />
      <PageHeader title={`TikTok ${organic ? 'orgánico' : 'Shop'} · ${current.label}`} showKpis={!!current.kpis} />
      <main className="flex-1 p-6">
        <Content key={current.id} onOpenTab={(next) => onTab(next as TikTokTab)} />
      </main>
    </ForcedChannel>
  )
}
