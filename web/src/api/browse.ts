import { api } from './client'
import type { BrowseTree, CurationState, FilesPage } from './types'

/** Fast folders-only tree (no per-file image opens) for the sidebar + counts. */
export function fetchFolders() {
  return api.get<BrowseTree>('/api/browse/folders')
}

export type FileFilter = 'all' | 'favorites' | 'hidden'

export interface FilesQuery {
  folder?: string
  q?: string
  filter?: FileFilter
  offset?: number
  limit?: number
}

/** A page of image files, scoped/filtered/paginated server-side. */
export function fetchFiles({ folder = '', q = '', filter = 'all', offset = 0, limit = 200 }: FilesQuery) {
  const params = new URLSearchParams({
    folder,
    q,
    filter,
    offset: String(offset),
    limit: String(limit),
  })
  return api.get<FilesPage>(`/api/browse/files?${params.toString()}`)
}

/** Favorited + excluded path lists. */
export function fetchCuration() {
  return api.get<CurationState>('/api/curation')
}

export interface CurationResult {
  path: string
  favorite: boolean
  excluded: boolean
}

export function setFavorite(path: string, value: boolean) {
  return api.post<CurationResult>('/api/curation/favorite', { path, value })
}

export function setExcluded(path: string, value: boolean) {
  return api.post<CurationResult>('/api/curation/exclude', { path, value })
}

/** URL for a lazily-loaded thumbnail of an image path. */
export function thumbUrl(path: string) {
  return '/thumbnail/' + path.split('/').map(encodeURIComponent).join('/')
}

/** URL for the full processed image. */
export function imageUrl(path: string) {
  return '/api/image/' + path.split('/').map(encodeURIComponent).join('/')
}
