import { api } from './client'
import type { Metrics, Health, AppSettings, HaEntity } from './types'

export function fetchMetrics() {
  return api.get<Metrics>('/metrics')
}
export function fetchHealth() {
  return api.get<Health>('/health')
}
export function testHomeAssistant() {
  return api.get<{ success: boolean; error?: string | null }>('/api/homeassistant/test')
}

export function fetchSettings() {
  return api.get<AppSettings>('/api/settings')
}
export function updateSettings(patch: Record<string, unknown>) {
  return api.put<AppSettings>('/api/settings', patch)
}
export function fetchHaEntities(domain: string) {
  return api
    .get<{ entities: HaEntity[]; error?: string }>(`/api/homeassistant/entities?domain=${encodeURIComponent(domain)}`)
    .then((r) => r.entities)
}
