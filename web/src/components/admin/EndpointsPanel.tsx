import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { DisplayGroup, DisplayEndpoint } from '@/api/types'
import { endpointNav, deleteEndpoint } from '@/api/admin'
import { GROUPS_KEY } from '@/hooks/useAdminWs'
import { useCuration } from '@/hooks/useCuration'
import { useNow } from '@/hooks/useNow'
import { IconButton, PrevIcon, NextIcon, SlidersIcon, TrashIcon, HeartIcon, EyeOffIcon, HistoryIcon, useConfirm } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { cn } from '@/lib/cn'
import { EndpointOverrideModal } from './EndpointOverrideModal'
import { HistoryModal } from './HistoryModal'
import styles from '@/pages/Admin.module.css'

export function EndpointsPanel({ group }: { group: DisplayGroup }) {
  const [editing, setEditing] = useState<DisplayEndpoint | null>(null)
  const [historyEp, setHistoryEp] = useState<string | null>(null)
  const [confirm, confirmModal] = useConfirm()
  const { favSet, hiddenSet, toggleFavorite, toggleHidden } = useCuration()

  // WS pushes are the primary refresh; invalidate on settle as the fallback
  // (endpoint-level nav doesn't always trigger a GROUP_UPDATED push).
  const qc = useQueryClient()
  const nav = useMutation({
    mutationFn: ({ id, dir }: { id: string; dir: 'next' | 'previous' }) =>
      endpointNav(group.group_id, id, dir),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Failed'),
    onSettled: () => qc.invalidateQueries({ queryKey: GROUPS_KEY }),
  })
  const del = useMutation({
    mutationFn: (id: string) => deleteEndpoint(group.group_id, id),
    onSuccess: () => toast.success('Endpoint removed'),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Failed'),
    onSettled: () => qc.invalidateQueries({ queryKey: GROUPS_KEY }),
  })

  const eps = group.endpoints ?? []
  // "Online" = an endpoint with a connected browser whose heartbeat is fresh.
  const now = useNow(5000)
  const liveIds = new Set(
    (group.browsers ?? [])
      .filter((b) => b.last_heartbeat && (now - new Date(b.last_heartbeat).getTime()) / 1000 <= 60)
      .map((b) => b.endpoint_id),
  )
  const online = eps.filter((e) => liveIds.has(e.endpoint_id)).length
  const offline = eps.length - online

  return (
    <details className={styles.endpoints}>
      <summary className={styles.endpointsSummary}>
        Endpoints <span className={styles.tabCount}>{eps.length}</span>
        {eps.length > 0 && (
          <span className={styles.epSummaryMeta}>
            <span style={{ color: online ? 'var(--color-success)' : undefined }}>{online} online</span>
            {' · '}
            <span style={{ color: offline ? 'var(--color-danger)' : undefined }}>{offline} offline</span>
          </span>
        )}
      </summary>

      {eps.length === 0 ? (
        <div className={styles.epEmpty}>
          No persistent endpoints. Open a display with <code>?endpoint=name</code> to create one.
        </div>
      ) : (
        eps.map((ep) => {
          const overrides = Object.keys(ep.settings_override ?? {}).length
          const img = ep.current_image
          return (
            <div key={ep.endpoint_id} className={styles.endpointRow}>
              <span className={cn(styles.epDot, ep.last_seen && styles.epDotOn)} />
              <div style={{ minWidth: 0 }}>
                <div className={styles.epName}>{ep.name || ep.endpoint_id}</div>
                <div className={styles.epMeta}>
                  {img ? img.split('/').pop() : 'no image'}
                  {overrides > 0 && ` · ${overrides} override${overrides > 1 ? 's' : ''}`}
                </div>
              </div>
              <div className={styles.epActions}>
                {img && (
                  <>
                    <IconButton
                      label="Favorite current"
                      size="sm"
                      active={favSet.has(img)}
                      onClick={() => toggleFavorite(img)}
                    >
                      <HeartIcon size={14} filled={favSet.has(img)} />
                    </IconButton>
                    <IconButton
                      label="Hide current"
                      size="sm"
                      active={hiddenSet.has(img)}
                      onClick={() => toggleHidden(img)}
                    >
                      <EyeOffIcon size={14} />
                    </IconButton>
                  </>
                )}
                <IconButton
                  label={`History (${ep.history_count})`}
                  size="sm"
                  active={ep.history_count > 0}
                  onClick={() => setHistoryEp(ep.endpoint_id)}
                >
                  <HistoryIcon size={14} />
                </IconButton>
                <IconButton
                  label="Previous"
                  size="sm"
                  onClick={() => nav.mutate({ id: ep.endpoint_id, dir: 'previous' })}
                >
                  <PrevIcon size={14} />
                </IconButton>
                <IconButton
                  label="Next"
                  size="sm"
                  onClick={() => nav.mutate({ id: ep.endpoint_id, dir: 'next' })}
                >
                  <NextIcon size={14} />
                </IconButton>
                <IconButton label="Overrides" size="sm" active={overrides > 0} onClick={() => setEditing(ep)}>
                  <SlidersIcon size={14} />
                </IconButton>
                <IconButton
                  label="Remove endpoint"
                  size="sm"
                  onClick={async () => {
                    if (
                      await confirm({
                        title: `Remove endpoint “${ep.name || ep.endpoint_id}”?`,
                        message: 'Its saved history and setting overrides are removed. A live display will re-register as a new anonymous endpoint.',
                        confirmLabel: 'Remove',
                      })
                    )
                      del.mutate(ep.endpoint_id)
                  }}
                >
                  <TrashIcon size={14} />
                </IconButton>
              </div>
            </div>
          )
        })
      )}

      {editing && (
        <EndpointOverrideModal
          groupId={group.group_id}
          endpoint={editing}
          groupSettings={group.settings}
          onClose={() => setEditing(null)}
        />
      )}

      {historyEp && (
        <HistoryModal
          groupId={group.group_id}
          endpoints={eps}
          initialEndpointId={historyEp}
          onClose={() => setHistoryEp(null)}
        />
      )}

      {confirmModal}
    </details>
  )
}
