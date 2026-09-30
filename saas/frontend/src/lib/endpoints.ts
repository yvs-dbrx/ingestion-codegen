import { apiFetch } from './api'
import type { MeOut, RegisterOut, TokenOut, UsageOut } from './types'

export const auth = {
  register: (body: { email: string; password: string; org_name: string }) =>
    apiFetch<RegisterOut>('/auth/register', { method: 'POST', body }),

  login: (email: string, password: string) =>
    apiFetch<TokenOut>('/auth/login', { method: 'POST', form: { username: email, password } }),
}

export const account = {
  me: (token: string) => apiFetch<MeOut>('/me', { token }),

  usage: (token: string, orgId: string) => apiFetch<UsageOut>('/org/usage', { token, orgId }),
}
