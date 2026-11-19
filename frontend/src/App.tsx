import { LayoutDashboard, Package, Upload } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import { Dashboard } from '@/pages/Dashboard'
import { Import } from '@/pages/Import'
import { Products } from '@/pages/Products'

type Page = 'dashboard' | 'products' | 'import'

const NAV: { id: Page; label: string; icon: ReactNode }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: <LayoutDashboard className="size-4" /> },
  { id: 'products', label: 'Productos', icon: <Package className="size-4" /> },
  { id: 'import', label: 'Importar', icon: <Upload className="size-4" /> },
]

export default function App() {
  const [page, setPage] = useState<Page>('dashboard')

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-56 shrink-0 bg-slate-800 p-4 text-slate-100 md:block">
        <div className="mb-6 text-lg font-bold tracking-tight">
          CHK <span className="text-amber-400">Profit</span>
        </div>
        <nav className="space-y-1">
          {NAV.map((n) => (
            <button
              key={n.id}
              onClick={() => setPage(n.id)}
              aria-current={page === n.id ? 'page' : undefined}
              className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors ${
                page === n.id ? 'bg-slate-700 text-white' : 'text-slate-300 hover:bg-slate-700/60'
              }`}
            >
              {n.icon}
              {n.label}
            </button>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <nav className="flex gap-1 border-b bg-slate-800 p-2 md:hidden">
          {NAV.map((n) => (
            <button
              key={n.id}
              onClick={() => setPage(n.id)}
              className={`rounded-md px-3 py-1.5 text-sm text-slate-100 ${page === n.id ? 'bg-slate-600' : ''}`}
            >
              {n.label}
            </button>
          ))}
        </nav>
        <main className="flex-1 p-6">
          {page === 'dashboard' && <Dashboard />}
          {page === 'products' && <Products />}
          {page === 'import' && <Import />}
        </main>
      </div>
    </div>
  )
}
