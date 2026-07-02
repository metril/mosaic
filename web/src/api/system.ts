import { api } from './client'
import type { Metrics, Health, AppSettings, HaEntity, LogRecord, ConfigImportResult } from './types'

/* ---- Logs ---------------------------------------------------------------- */
export function fetchLogs(params: { level?: string; q?: string; limit?: number } = {}) {
  const s = new URLSearchParams()
  if (params.level && params.level !== 'ALL') s.set('level', params.level)
  if (params.q) s.set('q', params.q)
  s.set('limit', String(params.limit ?? 500))
  return api.get<{ records: LogRecord[]; count: number }>(`/api/system/logs?${s}`)
}
export function logsDownloadUrl() {
  return '/api/system/logs?format=text&limit=2000'
}

/* ---- Config backup / restore --------------------------------------------- */
export async function downloadConfigExport(includeKeys: boolean) {
  const res = await fetch(`/api/system/config/export?include_keys=${includeKeys}`, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  })
  if (!res.ok) throw new Error(`Export failed (HTTP ${res.status})`)
  const blob = await res.blob()
  const cd = res.headers.get('Content-Disposition') || ''
  const m = cd.match(/filename="?([^"]+)"?/)
  const name = m ? m[1] : 'screensaver-config.json'
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
export function importConfig(bundle: unknown) {
  return api.post<ConfigImportResult>('/api/system/config/import', bundle)
}

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
export function refreshCaches(folder?: string) {
  const suffix = folder ? `?folder=${encodeURIComponent(folder)}` : ''
  return api.post<{ status: string; folder: string | null; groups_refreshed: Record<string, number> }>(
    `/api/system/refresh${suffix}`,
  )
}
export function fetchHaEntities(domain: string) {
  return api
    .get<{ entities: HaEntity[]; error?: string }>(`/api/homeassistant/entities?domain=${encodeURIComponent(domain)}`)
    .then((r) => r.entities)
}
