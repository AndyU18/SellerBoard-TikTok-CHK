import type { Session } from '@supabase/supabase-js'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { supabase } from '@/lib/supabase'

export interface Me {
  id: string | null
  email: string | null
  roles: string[]
  permissions: string[]
}

interface AuthState {
  /** undefined = todavía comprobando la sesión guardada */
  session: Session | null | undefined
  me: Me | null
  /** Mensaje si el backend rechazó al usuario (sin perfil, desactivado...). */
  accessError: string | null
  can: (permission: string) => boolean
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

async function fetchMe(token: string): Promise<{ me: Me | null; error: string | null }> {
  const res = await fetch('/api/me', { headers: { Authorization: `Bearer ${token}` } })
  if (res.ok) return { me: (await res.json()) as Me, error: null }
  let detail = `Error ${res.status}`
  try {
    detail = (await res.json()).detail ?? detail
  } catch {
    /* sin JSON */
  }
  return { me: null, error: typeof detail === 'string' ? detail : `Error ${res.status}` }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null | undefined>(undefined)
  const [me, setMe] = useState<Me | null>(null)
  const [accessError, setAccessError] = useState<string | null>(null)

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session))
    const { data } = supabase.auth.onAuthStateChange((_event, s) => setSession(s))
    return () => data.subscription.unsubscribe()
  }, [])

  const token = session?.access_token
  useEffect(() => {
    if (!token) {
      setMe(null)
      setAccessError(null)
      return
    }
    let cancelled = false
    fetchMe(token).then((r) => {
      if (cancelled) return
      setMe(r.me)
      setAccessError(r.error)
    })
    return () => {
      cancelled = true
    }
  }, [token])

  const signOut = useCallback(async () => {
    await supabase.auth.signOut()
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      session,
      me,
      accessError,
      can: (p) => Boolean(me && (me.permissions.includes('*') || me.permissions.includes(p))),
      signOut,
    }),
    [session, me, accessError, signOut],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth debe usarse dentro de <AuthProvider>')
  return ctx
}
