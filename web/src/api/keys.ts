import { api } from './client'
import type { ApiKey, AvailableResources } from './types'

export function listKeys() {
  return api.get<{ keys: ApiKey[] }>('/api/keys').then((r) => r.keys)
}

export function createKey(body: { name: string; scopes: string[] }) {
  return api.post<{ key: string; api_key: ApiKey; warning: string }>('/api/keys', body)
}

export function updateKey(id: string, body: { name?: string; enabled?: boolean; scopes?: string[] }) {
  return api.patch<ApiKey>(`/api/keys/${encodeURIComponent(id)}`, body)
}

export function revokeKey(id: string) {
  return api.del<{ status: string; key_id: string }>(`/api/keys/${encodeURIComponent(id)}`)
}

/** Reveal the full secret for a key so a display URL can embed it. `key` is null
 *  when the secret isn't recoverable (e.g. a migrated hash-only key). */
export function revealKey(id: string) {
  return api.get<{ key: string | null; recoverable: boolean }>(`/api/keys/${encodeURIComponent(id)}/secret`)
}

/** Rotate a key's secret in place (id/name/scopes preserved); returns the new
 *  secret once. */
export function rotateKey(id: string) {
  return api.post<{ key: string; api_key: ApiKey; warning: string }>(
    `/api/keys/${encodeURIComponent(id)}/rotate`,
    {},
  )
}

export function availableResources() {
  return api.get<AvailableResources>('/api/keys/available-resources')
}
