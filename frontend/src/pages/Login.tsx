import { useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { authConfigured, supabase } from '@/lib/supabase'

export function Login() {
  const [mode, setMode] = useState<'in' | 'up'>('in')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    setNotice('')
    try {
      if (mode === 'in') {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) setError(error.message)
      } else {
        const { data, error } = await supabase.auth.signUp({ email, password })
        if (error) setError(error.message)
        else if (!data.session) setNotice('Cuenta creada. Revisa tu correo para confirmarla y luego inicia sesión.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">
            CHK <span className="text-amber-500">Profit</span>
          </CardTitle>
          <CardDescription>{mode === 'in' ? 'Inicia sesión para continuar' : 'Crea tu cuenta'}</CardDescription>
        </CardHeader>
        <CardContent>
          {!authConfigured && (
            <p role="alert" className="mb-3 text-sm text-red-700">
              Falta configurar VITE_SUPABASE_URL y VITE_SUPABASE_PUBLISHABLE_KEY en frontend/.env.local.
            </p>
          )}
          <form onSubmit={submit} className="space-y-3">
            <Input
              type="email"
              required
              autoComplete="email"
              placeholder="correo@empresa.com"
              aria-label="Correo"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <Input
              type="password"
              required
              minLength={8}
              autoComplete={mode === 'in' ? 'current-password' : 'new-password'}
              placeholder="Contraseña (mínimo 8 caracteres)"
              aria-label="Contraseña"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <Button type="submit" className="w-full" disabled={busy || !authConfigured}>
              {busy ? 'Un momento…' : mode === 'in' ? 'Entrar' : 'Crear cuenta'}
            </Button>
          </form>
          {error && (
            <p role="alert" className="mt-3 text-sm text-red-700">
              {error}
            </p>
          )}
          {notice && (
            <p role="status" className="mt-3 text-sm text-green-700">
              {notice}
            </p>
          )}
          <button
            type="button"
            className="text-muted-foreground mt-4 text-sm underline"
            onClick={() => {
              setMode(mode === 'in' ? 'up' : 'in')
              setError('')
              setNotice('')
            }}
          >
            {mode === 'in' ? '¿Primera vez? Crear cuenta' : 'Ya tengo cuenta'}
          </button>
        </CardContent>
      </Card>
    </div>
  )
}
