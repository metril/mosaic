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

export function availableResources() {
  return api.get<AvailableResources>('/api/keys/available-resources')
}
