import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { api, type AccountRow, type Channel } from '@/lib/api'
import { presetRange, type Preset } from '@/lib/format'

/** Alcance de las pantallas: un canal y/o una tienda (cuenta). Se comparte entre todas las pantallas. */
export type ChannelFilter = Channel | 'all'

interface Scope {
  channel: ChannelFilter
  /** Nombre de la tienda ('' = todas). */
  account: string
  accounts: AccountRow[]
  /** Valor listo para los filtros de la API ('' = sin filtro). */
  channelParam: Channel | ''
  setChannel: (c: ChannelFilter) => void
  setAccount: (a: string) => void
  /** Período global (lo usan las pantallas con gráficas); `range` ya viene como fechas ISO. */
  preset: Preset
  range: { start?: string; end?: string }
  setPreset: (p: Preset) => void
}

// v2: el período por defecto pasó a "Todo"; la clave nueva descarta el "90 días" que quedó guardado antes.
const STORAGE_KEY = 'chk.scope.v2'
const ScopeContext = createContext<Scope | null>(null)

type Stored = { channel: ChannelFilter; account: string; preset: Preset }

function load(): Stored {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const v = JSON.parse(raw)
      return { channel: v.channel ?? 'all', account: v.account ?? '', preset: v.preset ?? 'all' }
    }
  } catch {
    /* sin almacenamiento: se usa el valor por defecto */
  }
  return { channel: 'all', account: '', preset: 'all' }
}

export function ScopeProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState(load)
  const [accounts, setAccounts] = useState<AccountRow[]>([])

  useEffect(() => {
    let alive = true
    api
      .accounts()
      .then((rows) => alive && setAccounts(rows))
      .catch(() => alive && setAccounts([]))
    return () => {
      alive = false
    }
  }, [])

  const save = useCallback((next: Stored) => {
    setState(next)
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      /* ignorado */
    }
  }, [])

  const value = useMemo<Scope>(
    () => ({
      channel: state.channel,
      account: state.account,
      accounts,
      channelParam: state.channel === 'all' ? '' : state.channel,
      // Al cambiar de canal, la tienda elegida deja de aplicar (era de otro canal).
      setChannel: (c) => save({ ...state, channel: c, account: '' }),
      setAccount: (a) => save({ ...state, account: a }),
      preset: state.preset,
      range: presetRange(state.preset),
      setPreset: (p) => save({ ...state, preset: p }),
    }),
    [state, accounts, save],
  )
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>
}

export function useScope(): Scope {
  const ctx = useContext(ScopeContext)
  if (!ctx) throw new Error('useScope debe usarse dentro de ScopeProvider')
  return ctx
}
