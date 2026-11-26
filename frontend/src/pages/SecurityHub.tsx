import { useState, type ComponentType } from 'react'

import type { SecurityTabId } from '@/lib/security'
import { SecurityCredentials } from '@/pages/security/SecurityCredentials'
import { SecurityOverview } from '@/pages/security/SecurityOverview'
import { SecurityRotation } from '@/pages/security/SecurityRotation'
import { SecurityScopes } from '@/pages/security/SecurityScopes'

interface Tab {
  id: SecurityTabId
  label: string
  group: string
}

/** El orden del arreglo es el de la barra; los grupos se dibujan cuando cambia `group`. */
const TABS: readonly Tab[] = [
  { id: 'overview', label: 'Resumen', group: 'Estado' },
  { id: 'scopes', label: 'Permisos', group: 'Revisar' },
  { id: 'credentials', label: 'Credenciales', group: 'Revisar' },
  { id: 'rotation', label: 'Rotación', group: 'Actuar' },
]

const STORAGE_KEY = 'chk.security.tab'
const DEFAULT_TAB: SecurityTabId = 'overview'
const VALID = new Set<string>(TABS.map((t) => t.id))

const isTab = (value: string | null): value is SecurityTabId => value !== null && VALID.has(value)

function initialTab(): SecurityTabId {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (isTab(saved)) return saved
  } catch {
    /* sin almacenamiento: se abre en el resumen */
  }
  return DEFAULT_TAB
}

/** Cada pestaña es una pantalla; solo el resumen usa `onOpenTab` para saltar a otra. */
const SCREENS: Record<SecurityTabId, ComponentType<{ onOpenTab: (tab: SecurityTabId) => void }>> = {
  overview: SecurityOverview,
  scopes: SecurityScopes,
  credentials: SecurityCredentials,
  rotation: SecurityRotation,
}

/**
 * Espacio "Seguridad": credenciales y permisos de todas las plataformas conectadas, con pestañas internas.
 * El backend nunca entrega valores de secretos y esta pantalla tampoco los pide ni los muestra.
 */
export function SecurityHub() {
  const [tab, setTab] = useState<SecurityTabId>(initialTab)

  const open = (next: SecurityTabId) => {
    setTab(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      /* ignorado */
    }
  }

  const current = TABS.find((t) => t.id === tab) ?? TABS[0]
  const Screen = SCREENS[current.id]

  return (
    <>
      <div className="border-b bg-white px-6 pt-4 dark:bg-slate-900">
        <h1 className="text-2xl font-semibold">Seguridad · {current.label}</h1>
        <p className="text-muted-foreground text-xs">Credenciales y permisos de todas las plataformas conectadas. Nunca se muestran valores de claves ni tokens.</p>
      </div>
      <nav aria-label="Secciones de Seguridad" className="flex flex-wrap items-center gap-x-1 gap-y-2 border-b bg-white px-6 py-2 dark:bg-slate-900">
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
    </>
  )
}
