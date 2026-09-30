import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError } from './api'
import { account, auth as authApi } from './endpoints'
import type { MeOut } from './types'

const TOKEN_KEY = 'refinaire.token'
const ORG_KEY = 'refinaire.activeOrgId'

interface AuthContextValue {
  token: string | null
  user: MeOut | null
  activeOrgId: string | null
  /** True while the initial session (token -> /me) is being restored on load. */
  isLoading: boolean
  setActiveOrgId: (orgId: string) => void
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, orgName: string) => Promise<void>
  logout: () => void
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

function pickActiveOrg(user: MeOut, remembered: string | null): string | null {
  if (remembered && user.memberships.some((m) => m.org.id === remembered)) return remembered
  return user.memberships[0]?.org.id ?? null
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY))
  const [user, setUser] = useState<MeOut | null>(null)
  const [activeOrgId, setActiveOrgIdState] = useState<string | null>(() => localStorage.getItem(ORG_KEY))
  const [isLoading, setIsLoading] = useState(true)

  const setActiveOrgId = useCallback((orgId: string) => {
    localStorage.setItem(ORG_KEY, orgId)
    setActiveOrgIdState(orgId)
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(ORG_KEY)
    setToken(null)
    setUser(null)
    setActiveOrgIdState(null)
  }, [])

  const loadUser = useCallback(
    async (currentToken: string) => {
      const me = await account.me(currentToken)
      setUser(me)
      const chosen = pickActiveOrg(me, localStorage.getItem(ORG_KEY))
      if (chosen) setActiveOrgId(chosen)
      return me
    },
    [setActiveOrgId],
  )

  useEffect(() => {
    let cancelled = false
    async function restore() {
      if (!token) {
        setIsLoading(false)
        return
      }
      try {
        await loadUser(token)
      } catch (err) {
        // An expired or invalid token — the user re-authenticates rather than seeing a blank screen.
        if (!cancelled) logout()
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    }
    restore()
    return () => {
      cancelled = true
    }
    // Only ever runs on mount / when the token identity changes from null <-> set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const login = useCallback(
    async (email: string, password: string) => {
      const { access_token } = await authApi.login(email, password)
      localStorage.setItem(TOKEN_KEY, access_token)
      setToken(access_token)
      await loadUser(access_token)
    },
    [loadUser],
  )

  const register = useCallback(
    async (email: string, password: string, orgName: string) => {
      const result = await authApi.register({ email, password, org_name: orgName })
      localStorage.setItem(TOKEN_KEY, result.access_token)
      localStorage.setItem(ORG_KEY, result.org.id)
      setToken(result.access_token)
      await loadUser(result.access_token)
    },
    [loadUser],
  )

  const refreshUser = useCallback(async () => {
    if (token) await loadUser(token)
  }, [token, loadUser])

  const value = useMemo<AuthContextValue>(
    () => ({ token, user, activeOrgId, isLoading, setActiveOrgId, login, register, logout, refreshUser }),
    [token, user, activeOrgId, isLoading, setActiveOrgId, login, register, logout, refreshUser],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}

export { ApiError }
