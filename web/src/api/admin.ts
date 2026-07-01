import { api } from './client'
import type {
  DisplayGroup,
  DisplayGroupSettings,
  DisplayEndpoint,
  EndpointHistory,
  EndpointCaption,
} from './types'

/** Server-built caption for an endpoint's current image (admin display preview). */
export function fetchEndpointCaption(groupId: string, endpointId: string) {
  return api.get<EndpointCaption>(
    `/api/display/groups/${encodeURIComponent(groupId)}/endpoints/${encodeURIComponent(endpointId)}/caption`,
  )
}

/* ---- Groups -------------------------------------------------------------- */

export function listGroups() {
  return api.get<DisplayGroup[]>('/api/display/groups')
}

export type GroupCreate = { group_id: string } & Partial<DisplayGroupSettings>
export function createGroup(body: GroupCreate) {
  return api.post<DisplayGroup>('/api/display/groups', body)
}

export function updateGroup(id: string, body: Partial<DisplayGroupSettings>) {
  return api.patch<DisplayGroup>(`/api/display/groups/${encodeURIComponent(id)}`, body)
}

export function deleteGroup(id: string) {
  return api.del<{ deleted: string }>(`/api/display/groups/${encodeURIComponent(id)}`)
}

export function cloneGroup(id: string, new_group_id: string) {
  return api.post<DisplayGroup>(`/api/display/groups/${encodeURIComponent(id)}/clone`, {
    new_group_id,
  })
}

/* ---- Playback ------------------------------------------------------------ */

export type PlaybackAction =
  | 'pause'
  | 'play'
  | 'next'
  | 'previous'
  | 'rewind-all'
  | 'forward-all'
  | 'refresh'

export function groupAction(id: string, action: PlaybackAction) {
  return api.post(`/api/display/groups/${encodeURIComponent(id)}/${action}`)
}

/* ---- Endpoints ----------------------------------------------------------- */

export function updateEndpoint(
  groupId: string,
  endpointId: string,
  body: {
    name?: string | null
    is_persistent?: boolean | null
    settings_override?: Partial<DisplayGroupSettings> | null
  },
) {
  return api.patch<DisplayEndpoint>(
    `/api/display/groups/${encodeURIComponent(groupId)}/endpoints/${encodeURIComponent(endpointId)}`,
    body,
  )
}

export function deleteEndpoint(groupId: string, endpointId: string) {
  return api.del(
    `/api/display/groups/${encodeURIComponent(groupId)}/endpoints/${encodeURIComponent(endpointId)}`,
  )
}

export function endpointNav(groupId: string, endpointId: string, dir: 'next' | 'previous') {
  return api.post<{ current_image: string | null }>(
    `/api/display/groups/${encodeURIComponent(groupId)}/endpoints/${encodeURIComponent(endpointId)}/${dir}`,
  )
}

/* ---- History ------------------------------------------------------------- */

export function fetchEndpointHistory(groupId: string, endpointId: string) {
  return api.get<EndpointHistory>(
    `/api/display/groups/${encodeURIComponent(groupId)}/endpoints/${encodeURIComponent(endpointId)}/history`,
  )
}

/** Jump the live display back to a previously shown image (must be in history). */
export function endpointJump(groupId: string, endpointId: string, imagePath: string) {
  return api.post<{ status: string; image_path: string }>(
    `/api/display/groups/${encodeURIComponent(groupId)}/endpoints/${encodeURIComponent(endpointId)}/jump`,
    { image_path: imagePath },
  )
}
