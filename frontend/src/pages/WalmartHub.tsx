import { useState, type ComponentType } from 'react'

import { PageHeader } from '@/components/PageHeader'
import { WalmartScopeBar } from '@/components/walmart/WalmartScopeBar'
import { ForcedChannel } from '@/lib/scope'
import { Inventory } from '@/pages/Inventory'
import { Returns } from '@/pages/Returns'
import { WalmartFeeds } from '@/pages/walmart/WalmartFeeds'
import { WalmartListingQuality } from '@/pages/walmart/WalmartListingQuality'
import { WalmartOpenOrders } from '@/pages/walmart/WalmartOpenOrders'
import { WalmartOrders } from '@/pages/walmart/WalmartOrders'
import { WalmartOverview } from '@/pages/walmart/WalmartOverview'
import { WalmartPayments } from '@/pages/walmart/WalmartPayments'
import { WalmartPerformance } from '@/pages/walmart/WalmartPerformance'
import { WalmartReports } from '@/pages/walmart/WalmartReports'
import { WalmartRepricer } from '@/pages/walmart/WalmartRepricer'
import { WalmartShipping } from '@/pages/walmart/WalmartShipping'
import { WalmartWfs } from '@/pages/walmart/WalmartWfs'

type TabId =
  | 'overview'
  | 'orders'
  | 'openOrders'
  | 'returns'
  | 'inventory'
  | 'wfs'
  | 'performance'
  | 'payments'
  | 'quality'
  | 'reports'
  | 'repricer'
  | 'shipping'
  | 'feeds'

type Tab = {
  id: TabId
  label: string
  group: string
  /** Si la pestaña se ve con la franja de indicadores de ventas arriba (las de catálogo y cargas no la necesitan). */
  kpis: boolean
}

/** El orden del arreglo es el de la barra; los grupos se dibujan cuando cambia `group`. */
const TABS: Tab[] = [
  { id: 'overview', label: 'Resumen', group: 'Operación', kpis: true },
  { id: 'orders', label: 'Pedidos', group: 'Operación', kpis: true },
  { id: 'openOrders', label: 'Por surtir', group: 'Operación', kpis: false },
  { id: 'returns', label: 'Devoluciones', group: 'Operación', kpis: true },
  { id: 'inventory', label: 'Listados e inventario', group: 'Catálogo', kpis: false },
  { id: 'wfs', label: 'Inventario WFS', group: 'Catálogo', kpis: false },
  { id: 'quality', label: 'Calidad del listado', group: 'Catálogo', kpis: false },
  { id: 'repricer', label: 'Repricer', group: 'Catálogo', kpis: false },
  { id: 'reports', label: 'Reportes', group: 'Catálogo', kpis: false },
  { id: 'feeds', label: 'Estado de cargas', group: 'Catálogo', kpis: false },
  { id: 'performance', label: 'Desempeño del vendedor', group: 'Cuenta y dinero', kpis: false },
  { id: 'payments', label: 'Pagos y comisiones', group: 'Cuenta y dinero', kpis: true },
  { id: 'shipping', label: 'Plantillas de envío', group: 'Cuenta y dinero', kpis: false },
]

const STORAGE_KEY = 'chk.walmart.tab'
const VALID = new Set<string>(TABS.map((t) => t.id))

function initialTab(): TabId {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved && VALID.has(saved)) return saved as TabId
  } catch {
    /* sin almacenamiento: se abre en el resumen */
  }
  return 'overview'
}

/** Cada pestaña es una pantalla; las de Walmart reciben `onOpenTab` para saltar a otra pestaña. */
const SCREENS: Record<TabId, ComponentType<{ onOpenTab?: (tab: string) => void }>> = {
  overview: WalmartOverview,
  orders: WalmartOrders,
  openOrders: WalmartOpenOrders,
  returns: Returns,
  inventory: Inventory,
  wfs: WalmartWfs,
  quality: WalmartListingQuality,
  repricer: WalmartRepricer,
  reports: WalmartReports,
  feeds: WalmartFeeds,
  performance: WalmartPerformance,
  payments: WalmartPayments,
  shipping: WalmartShipping,
}

/**
 * Espacio "Walmart": un solo apartado del menú lateral con pestañas internas. Todo lo que se muestra aquí está
 * filtrado a Walmart (`ForcedChannel`), también las pantallas que se comparten con los demás canales.
 */
export function WalmartHub() {
  const [tab, setTab] = useState<TabId>(initialTab)

  const open = (next: string) => {
    if (!VALID.has(next)) return
    setTab(next as TabId)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      /* ignorado */
    }
  }

  const current = TABS.find((t) => t.id === tab) ?? TABS[0]
  const Screen = SCREENS[current.id]

  return (
    <ForcedChannel channel="walmart">
      <WalmartScopeBar />
      <PageHeader title={`Walmart · ${current.label}`} showKpis={current.kpis} />
      <nav aria-label="Secciones de Walmart" className="flex flex-wrap items-center gap-x-1 gap-y-2 border-b bg-white px-6 py-2 dark:bg-slate-900">
        {TABS.map((t, i) => (
          <div key={t.id} className="flex items-center">
            {(i === 0 || TABS[i - 1].group !== t.group) && (
              <span className="text-muted-foreground mr-2 ml-3 text-[11px] font-semibold tracking-wider uppercase first:ml-0">{t.group}</span>
            )}
            <button
              type="button"
              onClick={() => open(t.id)}
              aria-current={t.id === tab ? 'page' : undefined}
              className={`rounded-md px-3 py-1.5 text-sm transition-colors ${t.id === tab ? 'bg-blue-600 text-white' : 'text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800'}`}
            >
              {t.label}
            </button>
          </div>
        ))}
      </nav>
      <main className="flex-1 p-6">
        <Screen key={current.id} onOpenTab={open} />
      </main>
    </ForcedChannel>
  )
}
