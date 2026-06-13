import { api } from './client'
import type { Metrics, Health } from './types'

export function fetchMetrics() {
  return api.get<Metrics>('/metrics')
}
export function fetchHealth() {
  return api.get<Health>('/health')
}
export function testHomeAssistant() {
  return api.get<{ success: boolean; error?: string | null }>('/api/homeassistant/test')
}
