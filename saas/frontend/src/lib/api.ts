/** Thin fetch wrapper over the FastAPI backend. Attaches the bearer token and the active
 * org header automatically; every endpoint-specific call lives in api/*.ts, not here. */

const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number
  detail: string
  problems?: string[]

  constructor(status: number, body: unknown) {
    const detail =
      typeof body === 'object' && body !== null && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : `Request failed with status ${status}`
    super(detail)
    this.status = status
    this.detail = detail
    this.problems =
      typeof body === 'object' && body !== null && 'problems' in body
        ? ((body as { problems: unknown }).problems as string[])
        : undefined
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  body?: unknown
  form?: Record<string, string> // application/x-www-form-urlencoded (only the login endpoint needs this)
  token?: string | null
  orgId?: string | null
}

export async function apiFetch<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {}
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`
  if (opts.orgId) headers['X-Org-Id'] = opts.orgId

  let body: BodyInit | undefined
  if (opts.form) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded'
    body = new URLSearchParams(opts.form)
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }

  const res = await fetch(`${BASE_URL}${path}`, { method: opts.method ?? 'GET', headers, body })

  if (res.status === 204) return undefined as T

  const text = await res.text()
  const parsed = text ? JSON.parse(text) : undefined

  if (!res.ok) throw new ApiError(res.status, parsed)
  return parsed as T
}
