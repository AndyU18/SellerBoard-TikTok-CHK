/**
 * Secciones del espacio TikTok. Una sola lista alimenta el menú lateral desplegable (App.tsx) y el hub
 * (TikTokHub.tsx): agregar una sección es agregarla aquí y en el mapa de pantallas del hub.
 */
export type TikTokTab =
  | 'overview'
  | 'orders'
  | 'returns'
  | 'inventory'
  | 'performance'
  | 'live'
  | 'creatives'
  | 'samples'
  | 'sampleStrategy'
  | 'creatorProfit'
  | 'settlements'
  | 'settlementAging'
  | 'reconciliation'
  | 'productPerformance'
  | 'skuPerformance'
  | 'catalog'
  | 'promotions'
  | 'coupons'
  | 'packages'
  | 'payments'
  | 'withdrawals'
  | 'warehouses'
  | 'webhooks'
  | 'organicOverview'
  | 'organicVideos'
  | 'organicPerformance'
  | 'organicTimes'
  | 'organicHashtags'
  | 'organicVsShop'
  | 'organicConnection'
  | 'creatorProfile'
  | 'creatorOpportunities'
  | 'creatorInsights'
  | 'creatorOrders'
  | 'creatorShowcase'

export type TikTokMenuItem = {
  id: TikTokTab
  label: string
  group: string
  /** Si la pantalla se ve con la franja de indicadores de ventas arriba. */
  kpis?: boolean
}

/** El orden es el del menú; los grupos se dibujan cuando cambia `group`. */
export const TIKTOK_MENU: TikTokMenuItem[] = [
  { id: 'overview', label: 'Resumen', group: 'Tienda', kpis: true },
  { id: 'orders', label: 'Pedidos', group: 'Tienda', kpis: true },
  { id: 'returns', label: 'Devoluciones', group: 'Tienda', kpis: true },
  { id: 'packages', label: 'Paquetes', group: 'Tienda' },
  { id: 'inventory', label: 'Listados e inventario', group: 'Tienda' },
  { id: 'warehouses', label: 'Almacenes', group: 'Tienda' },

  { id: 'productPerformance', label: 'Rendimiento por producto', group: 'Catálogo y ventas', kpis: true },
  { id: 'skuPerformance', label: 'Rendimiento por SKU', group: 'Catálogo y ventas', kpis: true },
  { id: 'catalog', label: 'Catálogo (imágenes)', group: 'Catálogo y ventas' },
  { id: 'promotions', label: 'Promociones', group: 'Catálogo y ventas' },
  { id: 'coupons', label: 'Cupones', group: 'Catálogo y ventas' },
  { id: 'performance', label: 'Embudo y tráfico', group: 'Catálogo y ventas' },

  { id: 'live', label: 'LIVE', group: 'Creadores y contenido' },
  { id: 'creatives', label: 'Creativos', group: 'Creadores y contenido' },
  { id: 'samples', label: 'Muestras', group: 'Creadores y contenido' },
  { id: 'sampleStrategy', label: 'Política de muestras', group: 'Creadores y contenido' },
  { id: 'creatorProfit', label: 'Rentabilidad por creador', group: 'Creadores y contenido' },

  { id: 'settlements', label: 'Desglose de liquidación', group: 'Dinero', kpis: true },
  { id: 'settlementAging', label: 'Dinero atorado', group: 'Dinero' },
  { id: 'reconciliation', label: 'Conciliación', group: 'Dinero' },
  { id: 'payments', label: 'Pagos a tu banco', group: 'Dinero' },
  { id: 'withdrawals', label: 'Retiros', group: 'Dinero' },
  { id: 'webhooks', label: 'Webhooks', group: 'Dinero' },

  { id: 'organicOverview', label: 'Resumen', group: 'TikTok orgánico' },
  { id: 'organicVideos', label: 'Videos', group: 'TikTok orgánico' },
  { id: 'organicPerformance', label: 'Rendimiento', group: 'TikTok orgánico' },
  { id: 'organicTimes', label: 'Mejores horarios', group: 'TikTok orgánico' },
  { id: 'organicHashtags', label: 'Hashtags y duración', group: 'TikTok orgánico' },
  { id: 'organicVsShop', label: 'Orgánico vs ventas', group: 'TikTok orgánico' },
  { id: 'organicConnection', label: 'Conexión', group: 'TikTok orgánico' },

  { id: 'creatorOpportunities', label: 'Oportunidades de productos', group: 'TikTok Creator' },
  { id: 'creatorInsights', label: 'Panorama de afiliados', group: 'TikTok Creator' },
  { id: 'creatorOrders', label: 'Pedidos de afiliado', group: 'TikTok Creator' },
  { id: 'creatorShowcase', label: 'Vitrina', group: 'TikTok Creator' },
  { id: 'creatorProfile', label: 'Cuenta de creador', group: 'TikTok Creator' },
]

export const TIKTOK_DEFAULT_TAB: TikTokTab = 'overview'
const VALID = new Set<string>(TIKTOK_MENU.map((t) => t.id))
export const isTikTokTab = (value: string): value is TikTokTab => VALID.has(value)

const STORAGE_KEY = 'chk.tiktok.tab'

/** Última sección abierta (si el navegador no deja guardar, se abre en el resumen). */
export function loadTikTokTab(): TikTokTab {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved && isTikTokTab(saved)) return saved
  } catch {
    /* sin almacenamiento */
  }
  return TIKTOK_DEFAULT_TAB
}

export function saveTikTokTab(tab: TikTokTab): void {
  try {
    localStorage.setItem(STORAGE_KEY, tab)
  } catch {
    /* ignorado */
  }
}
