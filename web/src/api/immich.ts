import { api } from './client'
import type {
  ImmichPull,
  PullResult,
  ImmichAlbum,
  ImmichPerson,
  ImmichTestResult,
} from './types'

export function listPulls() {
  return api.get<{ pulls: ImmichPull[] }>('/api/immich/pulls').then((r) => r.pulls)
}

export interface PullSave {
  name: string
  server_url: string
  api_key?: string | null
  target_folder: string
  count?: number
  album_ids?: string[] | null
  album_filter_mode?: 'all' | 'any'
  person_ids?: string[] | null
  person_filter_mode?: 'all' | 'any'
  is_favorite?: boolean | null
  taken_after?: string | null
  taken_before?: string | null
  media_type?: 'IMAGE' | 'VIDEO'
  replace_existing?: boolean
  cron_expression?: string | null
  cron_timezone?: string | null
  protect_favorites?: boolean
  skip_excluded?: boolean
  storage_mode?: 'download' | 'stream'
  stream_source?: 'preview' | 'fullsize' | 'original'
  stream_max_w?: number
  stream_max_h?: number
  stream_format?: 'original' | 'jpeg' | 'png' | 'webp'
  stream_quality?: number
}

export function savePull(body: PullSave) {
  return api.post<{ message: string }>('/api/immich/pulls', body)
}

export function deletePull(name: string) {
  return api.del<{ message: string }>(`/api/immich/pulls/${encodeURIComponent(name)}`)
}

export function triggerPull(name: string, delta = false) {
  const suffix = delta ? '?delta=true' : ''
  return api.post<{ status: string; delta?: boolean; result: PullResult }>(
    `/api/immich/pull/${encodeURIComponent(name)}${suffix}`,
  )
}

export function cancelPull(name: string) {
  return api.post<{ status: string; result: PullResult }>(
    `/api/immich/pull/${encodeURIComponent(name)}/cancel`,
  )
}

export function pullStatus(name: string) {
  return api.get<PullResult | { status: 'idle'; pull_name: string }>(
    `/api/immich/pull/${encodeURIComponent(name)}/status`,
  )
}

export function testConnection(body: { server_url: string; api_key?: string | null; pull_name?: string }) {
  return api.post<ImmichTestResult>('/api/immich/test-connection', body)
}

function q(params: Record<string, string | undefined>) {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) s.set(k, v)
  return s.toString()
}

export function listAlbums(p: { server_url: string; api_key?: string; pull_name?: string }) {
  return api.get<{ albums: ImmichAlbum[] }>(`/api/immich/albums?${q(p)}`).then((r) => r.albums)
}

export function listPeople(p: { server_url: string; api_key?: string; pull_name?: string }) {
  return api.get<{ people: ImmichPerson[] }>(`/api/immich/people?${q(p)}`).then((r) => r.people)
}
