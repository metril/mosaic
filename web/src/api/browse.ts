import { api } from './client'
import type { BrowseTree, CurationState } from './types'

/** The full recursive directory tree (root node under `.tree` + totals). */
export function fetchTree() {
  return api.get<BrowseTree>('/browse?format=json')
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
