import { getCsrfToken, clearCsrfToken } from './csrf'

export class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

type Method = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
const MUTATING = new Set<Method>(['POST', 'PATCH', 'PUT', 'DELETE'])

/** Redirect the browser to OIDC login, preserving where we were. Login is
 *  server-rendered, so a full navigation is correct here. */
function redirectToLogin() {
  const here = window.location.pathname + window.location.search
  window.location.assign('/auth/login?redirect=' + encodeURIComponent(here))
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json()
    // FastAPI uses {detail: ...} or this app's {error, message}
    if (typeof body?.detail === 'string') return body.detail
    if (body?.message) return body.message
    if (body?.error) return body.error
    return JSON.stringify(body)
  } catch {
    return res.statusText
  }
}

async function request<T>(method: Method, path: string, body?: unknown, _retried = false): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const init: RequestInit = { method, headers, credentials: 'same-origin' }

  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  if (MUTATING.has(method)) {
    headers['X-CSRF-Token'] = await getCsrfToken()
  }

  const res = await fetch(path, init)

  if (res.status === 401) {
    redirectToLogin()
    throw new ApiError(401, 'Authentication required')
  }

  // CSRF token may have rotated with the session — refresh once and retry.
  if (res.status === 403 && MUTATING.has(method) && !_retried) {
    clearCsrfToken()
    await getCsrfToken(true)
    return request<T>(method, path, body, true)
  }

  if (!res.ok) {
    throw new ApiError(res.status, await parseError(res))
  }

  if (res.status === 204) return undefined as T
  const ct = res.headers.get('Content-Type') ?? ''
  if (ct.includes('application/json')) return (await res.json()) as T
  return (await res.text()) as unknown as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  del: <T>(path: string, body?: unknown) => request<T>('DELETE', path, body),
}
