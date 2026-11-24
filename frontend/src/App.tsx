import {
  BarChart3,
  Banknote,
  Bell,
  Boxes,
  Calculator,
  Clock,
  Coins,
  DollarSign,
  Filter,
  GitCompareArrows,
  Gift,
  LayoutDashboard,
  Download,
  Link2,
  ListOrdered,
  LogOut,
  Mail,
  Megaphone,
  Package,
  PackageCheck,
  Radio,
  Scale,
  ScrollText,
  Ship,
  ShieldCheck,
  Sparkles,
  Tag,
  TrendingUp,
  Truck,
  Undo2,
  Upload,
  Users as UsersIcon,
  Video,
  Wallet,
  Warehouse,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { ScopeBar } from '@/components/ScopeBar'
import { loadAlerts } from '@/lib/alerts'
import { useAuth } from '@/lib/auth'
import { ScopeProvider } from '@/lib/scope'
import { useAsync } from '@/lib/useAsync'
import { Alerts } from '@/pages/Alerts'
import { Assistant } from '@/pages/Assistant'
import { Cashflow } from '@/pages/Cashflow'
import { DataQuality } from '@/pages/DataQuality'
import { ExportCenter } from '@/pages/Export'
import { Notifications } from '@/pages/Notifications'
import { SkuManager } from '@/pages/SkuManager'
import { Costs } from '@/pages/Costs'
import { Creatives } from '@/pages/Creatives'
import { Dashboard } from '@/pages/Dashboard'
import { Dayparting } from '@/pages/Dayparting'
import { Forecast } from '@/pages/Forecast'
import { Funnel } from '@/pages/Funnel'
import { Import } from '@/pages/Import'
import { Inventory } from '@/pages/Inventory'
import { Live } from '@/pages/Live'
import { Login } from '@/pages/Login'
import { Logistics } from '@/pages/Logistics'
import { Pareto } from '@/pages/Pareto'
import { Pnl } from '@/pages/Pnl'
import { Pricing } from '@/pages/Pricing'
import { Products } from '@/pages/Products'
import { Promotions } from '@/pages/Promotions'
import { PageHeader } from '@/components/PageHeader'
import { Ads } from '@/pages/Ads'
import { Reconciliation } from '@/pages/Reconciliation'
import { RedoBilling } from '@/pages/RedoBilling'
import { RedoCharges } from '@/pages/RedoCharges'
import { RedoCosts } from '@/pages/RedoCosts'
import { RedoOverview } from '@/pages/RedoOverview'
import { Reorder } from '@/pages/Reorder'
import { Returns } from '@/pages/Returns'
import { Samples } from '@/pages/Samples'
import { Tariffs } from '@/pages/Tariffs'
import { Users } from '@/pages/Users'
import { Versus } from '@/pages/Versus'

type Page =
  | 'dashboard'
  | 'alerts'
  | 'assistant'
  | 'forecast'
  | 'pnl'
  | 'skus'
  | 'quality'
  | 'notifications'
  | 'export'
  | 'users'
  | 'pareto'
  | 'versus'
  | 'pricing'
  | 'tariffs'
  | 'promotions'
  | 'dayparting'
  | 'cashflow'
  | 'reconciliation'
  | 'ads'
  | 'redo'
  | 'redoCosts'
  | 'redoBilling'
  | 'redoCharges'
  | 'returns'
  | 'logistics'
  | 'inventory'
  | 'reorder'
  | 'samples'
  | 'creatives'
  | 'funnel'
  | 'live'
  | 'products'
  | 'costs'
  | 'import'

type NavItem = { id: Page; label: string; icon: ReactNode; permission: string; group: string }

const icon = 'size-4'
// El orden del arreglo es el del menú; los grupos se dibujan cuando cambia `group`.
const NAV: NavItem[] = [
  { group: 'Resumen', id: 'dashboard', label: 'Resumen ejecutivo', icon: <LayoutDashboard className={icon} />, permission: 'profit.view' },
  { group: 'Resumen', id: 'alerts', label: 'Alertas', icon: <Bell className={icon} />, permission: 'profit.view' },
  { group: 'Resumen', id: 'assistant', label: 'Asistente', icon: <Sparkles className={icon} />, permission: 'profit.view' },
  { group: 'Resumen', id: 'forecast', label: 'Pronóstico y metas', icon: <TrendingUp className={icon} />, permission: 'profit.view' },
  { group: 'Resumen', id: 'pnl', label: 'Estado de resultados', icon: <ScrollText className={icon} />, permission: 'profit.view' },
  { group: 'Rentabilidad', id: 'pareto', label: 'Pareto ABC', icon: <BarChart3 className={icon} />, permission: 'profit.view' },
  { group: 'Rentabilidad', id: 'versus', label: 'Versus', icon: <GitCompareArrows className={icon} />, permission: 'profit.view' },
  { group: 'Rentabilidad', id: 'pricing', label: 'Precios', icon: <DollarSign className={icon} />, permission: 'profit.view' },
  { group: 'Rentabilidad', id: 'tariffs', label: 'Aranceles', icon: <Ship className={icon} />, permission: 'profit.view' },
  { group: 'Rentabilidad', id: 'ads', label: 'Publicidad (TikTok Ads)', icon: <Megaphone className={icon} />, permission: 'profit.view' },
  { group: 'Rentabilidad', id: 'promotions', label: 'Promociones', icon: <Tag className={icon} />, permission: 'profit.view' },
  { group: 'Operación', id: 'inventory', label: 'Inventario', icon: <Warehouse className={icon} />, permission: 'profit.view' },
  { group: 'Operación', id: 'reorder', label: 'Reposición', icon: <Boxes className={icon} />, permission: 'profit.view' },
  { group: 'Operación', id: 'logistics', label: 'Logística', icon: <Truck className={icon} />, permission: 'profit.view' },
  { group: 'Operación', id: 'returns', label: 'Devoluciones', icon: <Undo2 className={icon} />, permission: 'profit.view' },
  { group: 'Operación', id: 'dayparting', label: 'Horas pico', icon: <Clock className={icon} />, permission: 'profit.view' },
  { group: 'Dinero', id: 'cashflow', label: 'Flujo de caja', icon: <Banknote className={icon} />, permission: 'profit.view' },
  { group: 'Dinero', id: 'reconciliation', label: 'Conciliación', icon: <Scale className={icon} />, permission: 'profit.view' },
  { group: 'Redo (etiquetas)', id: 'redo', label: 'Resumen de Redo', icon: <PackageCheck className={icon} />, permission: 'profit.view' },
  { group: 'Redo (etiquetas)', id: 'redoCosts', label: 'Costo por orden', icon: <Calculator className={icon} />, permission: 'profit.view' },
  { group: 'Redo (etiquetas)', id: 'redoBilling', label: 'Facturación y saldo', icon: <Wallet className={icon} />, permission: 'profit.view' },
  { group: 'Redo (etiquetas)', id: 'redoCharges', label: 'Libro de cargos', icon: <ListOrdered className={icon} />, permission: 'profit.view' },
  { group: 'Creadores y tráfico', id: 'samples', label: 'Muestras', icon: <Gift className={icon} />, permission: 'profit.view' },
  { group: 'Creadores y tráfico', id: 'creatives', label: 'Creativos', icon: <Video className={icon} />, permission: 'profit.view' },
  { group: 'Creadores y tráfico', id: 'funnel', label: 'Embudo', icon: <Filter className={icon} />, permission: 'profit.view' },
  { group: 'Creadores y tráfico', id: 'live', label: 'LIVE', icon: <Radio className={icon} />, permission: 'profit.view' },
  { group: 'Catálogo y datos', id: 'products', label: 'Productos', icon: <Package className={icon} />, permission: 'catalog.view' },
  { group: 'Catálogo y datos', id: 'skus', label: 'Gestor de SKU', icon: <Link2 className={icon} />, permission: 'catalog.view' },
  { group: 'Catálogo y datos', id: 'quality', label: 'Calidad de datos', icon: <ShieldCheck className={icon} />, permission: 'profit.view' },
  { group: 'Catálogo y datos', id: 'notifications', label: 'Notificaciones', icon: <Mail className={icon} />, permission: 'settings.view' },
  { group: 'Catálogo y datos', id: 'costs', label: 'Costos (COGS)', icon: <Coins className={icon} />, permission: 'catalog.view' },
  { group: 'Catálogo y datos', id: 'export', label: 'Exportar', icon: <Download className={icon} />, permission: 'profit.view' },
  { group: 'Catálogo y datos', id: 'import', label: 'Importar', icon: <Upload className={icon} />, permission: 'import.run' },
  { group: 'Administración', id: 'users', label: 'Usuarios', icon: <UsersIcon className={icon} />, permission: 'users.view' },
]

/** Pantallas sin franja de indicadores (administración o sin relación con ventas). */
const NO_KPI_PAGES: Page[] = ['users', 'notifications', 'assistant', 'skus', 'export', 'quality']

/** Cuántas alertas críticas (o, si no hay, de atención) hay; se comparte la petición con la pantalla. */
function AlertBadge() {
  const alerts = useAsync(() => loadAlerts(), [])
  const counts = alerts.data?.counts
  const critical = counts?.critical ?? 0
  const total = critical || (counts?.warning ?? 0)
  if (!total) return null
  return (
    <span
      className={`ml-auto rounded-full px-2 py-0.5 text-xs font-medium text-white ${critical ? 'bg-red-600' : 'bg-amber-500'}`}
      title={critical ? `${critical} alertas críticas` : `${total} alertas de atención`}
    >
      {total}
    </span>
  )
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="flex min-h-screen items-center justify-center p-6 text-center text-sm">{children}</div>
}

export default function App() {
  const { session, me, accessError, can, signOut } = useAuth()
  const [page, setPage] = useState<Page>('dashboard')

  if (session === undefined) return <Centered>Cargando…</Centered>
  if (session === null) return <Login />

  if (!me) {
    return (
      <Centered>
        <div className="max-w-sm space-y-3">
          <p>{accessError ?? 'Cargando tu perfil…'}</p>
          {accessError && (
            <Button variant="outline" onClick={signOut}>
              Cerrar sesión
            </Button>
          )}
        </div>
      </Centered>
    )
  }

  const nav = NAV.filter((n) => can(n.permission))
  if (nav.length === 0) {
    return (
      <Centered>
        <div className="max-w-sm space-y-3">
          <p className="font-medium">Tu cuenta está pendiente de aprobación</p>
          <p className="text-muted-foreground">
            Ya iniciaste sesión como {me.email}, pero todavía no tienes un rol. Pídele al Dueño que te lo asigne.
          </p>
          <Button variant="outline" onClick={signOut}>
            Cerrar sesión
          </Button>
        </div>
      </Centered>
    )
  }
  const current = nav.some((n) => n.id === page) ? page : nav[0].id
  // Las pantallas pueden mandar al usuario a otra (p. ej. Calidad de datos -> Costos); solo si tiene permiso.
  const navigate = (id: string) => {
    if (nav.some((n) => n.id === id)) setPage(id as Page)
  }

  return (
    <ScopeProvider>
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col bg-slate-800 p-4 text-slate-100 md:flex">
        <div className="mb-4 text-lg font-bold tracking-tight">
          CHK <span className="text-amber-400">Profit</span>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto pr-1" aria-label="Secciones">
          {nav.map((n, i) => (
            <div key={n.id}>
              {(i === 0 || nav[i - 1].group !== n.group) && (
                <div className="mt-3 mb-1 px-3 text-[11px] font-semibold tracking-wider text-slate-400 uppercase first:mt-0">
                  {n.group}
                </div>
              )}
              <button
                onClick={() => setPage(n.id)}
                aria-current={current === n.id ? 'page' : undefined}
                className={`flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-sm transition-colors ${
                  current === n.id ? 'bg-slate-700 text-white' : 'text-slate-300 hover:bg-slate-700/60'
                }`}
              >
                {n.icon}
                {n.label}
                {n.id === 'alerts' && <AlertBadge />}
              </button>
            </div>
          ))}
        </nav>
        <div className="border-t border-slate-700 pt-3 text-xs text-slate-300">
          <div className="truncate" title={me.email ?? ''}>
            {me.email}
          </div>
          <div className="text-slate-400">{me.roles.join(', ') || 'sin rol'}</div>
          <button onClick={signOut} className="mt-2 flex items-center gap-1 text-slate-300 hover:text-white">
            <LogOut className="size-3" /> Salir
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <nav className="flex items-center gap-1 overflow-x-auto border-b bg-slate-800 p-2 md:hidden">
          {nav.map((n) => (
            <button
              key={n.id}
              onClick={() => setPage(n.id)}
              className={`shrink-0 rounded-md px-3 py-1.5 text-sm text-slate-100 ${current === n.id ? 'bg-slate-600' : ''}`}
            >
              {n.label}
            </button>
          ))}
          <button onClick={signOut} aria-label="Salir" className="ml-auto p-1.5 text-slate-300">
            <LogOut className="size-4" />
          </button>
        </nav>
        {current !== 'import' && <ScopeBar />}
        {current !== 'import' && (
          <PageHeader
            title={nav.find((n) => n.id === current)?.label ?? ''}
            showKpis={!NO_KPI_PAGES.includes(current)}
          />
        )}
        <main className="flex-1 p-6">
          {current === 'dashboard' && <Dashboard />}
          {current === 'alerts' && <Alerts />}
          {current === 'assistant' && <Assistant />}
          {current === 'forecast' && <Forecast />}
          {current === 'skus' && <SkuManager />}
          {current === 'quality' && <DataQuality onNavigate={(p) => navigate(p)} />}
          {current === 'notifications' && <Notifications />}
          {current === 'export' && <ExportCenter />}
          {current === 'users' && <Users />}
          {current === 'pnl' && <Pnl />}
          {current === 'pareto' && <Pareto />}
          {current === 'versus' && <Versus />}
          {current === 'pricing' && <Pricing />}
          {current === 'tariffs' && <Tariffs />}
          {current === 'promotions' && <Promotions />}
          {current === 'dayparting' && <Dayparting />}
          {current === 'cashflow' && <Cashflow />}
          {current === 'reconciliation' && <Reconciliation />}
          {current === 'ads' && <Ads />}
          {current === 'redo' && <RedoOverview />}
          {current === 'redoCosts' && <RedoCosts />}
          {current === 'redoBilling' && <RedoBilling />}
          {current === 'redoCharges' && <RedoCharges />}
          {current === 'returns' && <Returns />}
          {current === 'logistics' && <Logistics />}
          {current === 'inventory' && <Inventory />}
          {current === 'reorder' && <Reorder />}
          {current === 'samples' && <Samples />}
          {current === 'creatives' && <Creatives />}
          {current === 'funnel' && <Funnel />}
          {current === 'live' && <Live />}
          {current === 'products' && <Products />}
          {current === 'costs' && <Costs />}
          {current === 'import' && <Import />}
        </main>
      </div>
    </div>
    </ScopeProvider>
  )
}
