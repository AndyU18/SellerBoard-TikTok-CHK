import { BarChart3, Coins, GitCompareArrows, LayoutDashboard, LogOut, Package, Upload } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'
import { Costs } from '@/pages/Costs'
import { Dashboard } from '@/pages/Dashboard'
import { Import } from '@/pages/Import'
import { Login } from '@/pages/Login'
import { Pareto } from '@/pages/Pareto'
import { Products } from '@/pages/Products'
import { Versus } from '@/pages/Versus'

type Page = 'dashboard' | 'pareto' | 'versus' | 'products' | 'costs' | 'import'

const NAV: { id: Page; label: string; icon: ReactNode; permission: string }[] = [
  { id: 'dashboard', label: 'Resumen ejecutivo', icon: <LayoutDashboard className="size-4" />, permission: 'profit.view' },
  { id: 'pareto', label: 'Pareto ABC', icon: <BarChart3 className="size-4" />, permission: 'profit.view' },
  { id: 'versus', label: 'Versus', icon: <GitCompareArrows className="size-4" />, permission: 'profit.view' },
  { id: 'products', label: 'Productos', icon: <Package className="size-4" />, permission: 'catalog.view' },
  { id: 'costs', label: 'Costos (COGS)', icon: <Coins className="size-4" />, permission: 'catalog.view' },
  { id: 'import', label: 'Importar', icon: <Upload className="size-4" />, permission: 'import.run' },
]

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

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-56 shrink-0 flex-col bg-slate-800 p-4 text-slate-100 md:flex">
        <div className="mb-6 text-lg font-bold tracking-tight">
          CHK <span className="text-amber-400">Profit</span>
        </div>
        <nav className="flex-1 space-y-1">
          {nav.map((n) => (
            <button
              key={n.id}
              onClick={() => setPage(n.id)}
              aria-current={current === n.id ? 'page' : undefined}
              className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors ${
                current === n.id ? 'bg-slate-700 text-white' : 'text-slate-300 hover:bg-slate-700/60'
              }`}
            >
              {n.icon}
              {n.label}
            </button>
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
        <nav className="flex items-center gap-1 border-b bg-slate-800 p-2 md:hidden">
          {nav.map((n) => (
            <button
              key={n.id}
              onClick={() => setPage(n.id)}
              className={`rounded-md px-3 py-1.5 text-sm text-slate-100 ${current === n.id ? 'bg-slate-600' : ''}`}
            >
              {n.label}
            </button>
          ))}
          <button onClick={signOut} aria-label="Salir" className="ml-auto p-1.5 text-slate-300">
            <LogOut className="size-4" />
          </button>
        </nav>
        <main className="flex-1 p-6">
          {current === 'dashboard' && <Dashboard />}
          {current === 'pareto' && <Pareto />}
          {current === 'versus' && <Versus />}
          {current === 'products' && <Products />}
          {current === 'costs' && <Costs />}
          {current === 'import' && <Import />}
        </main>
      </div>
    </div>
  )
}
