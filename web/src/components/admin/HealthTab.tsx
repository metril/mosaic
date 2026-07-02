import { memo, useState } from 'react'
import type { DisplayGroup, DisplayGroupSettings } from '@/api/types'
import { useGroups } from '@/hooks/useAdminWs'
import { useNow } from '@/hooks/useNow'
import { Card, EmptyState, Loading, Button, ActivityIcon, LoadError } from '@/components/ui'
import { cn } from '@/lib/cn'
import { DisplayPreviewModal } from './DisplayPreviewModal'
import styles from '@/pages/Admin.module.css'

type Status = 'online' | 'idle' | 'stale' | 'offline'

// Thresholds mirror the backend (client heartbeat is 30s).
const ONLINE_AFTER = 45
const STALE_AFTER = 60

function statusOf(lastHeartbeat: string | undefined, now: number): { status: Status; ageS: number | null } {
  if (!lastHeartbeat) return { status: 'offline', ageS: null }
  const age = (now - new Date(lastHeartbeat).getTime()) / 1000
  if (age <= ONLINE_AFTER) return { status: 'online', ageS: age }
  if (age <= STALE_AFTER) return { status: 'idle', ageS: age }
  return { status: 'stale', ageS: age }
}

const pillClass: Record<Status, string> = {
  online: styles.stCompleted,
  idle: styles.stRunning,
  stale: styles.stFailed,
  offline: styles.stIdle,
}

function fmtUptime(s: number | null): string {
  if (s == null) return '—'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h) return `${h}h ${m}m`
  if (m) return `${m}m`
  return `${Math.floor(s)}s`
}

interface PreviewTarget {
  groupId: string
  endpointId: string
  name: string | null
  currentImage: string | null
  settings: DisplayGroupSettings
}

export function HealthTab() {
  const { data: groups, isLoading, isError, refetch } = useGroups()
  // 5s resolution is plenty for heartbeat ages (status thresholds are 45/60s)
  // and avoids re-rendering every endpoint row once a second.
  const now = useNow(5000)
  const [preview, setPreview] = useState<PreviewTarget | null>(null)

  if (isLoading) return <Loading label="Loading display health…" />
  if (isError && !groups) return <LoadError title="Couldn’t load display health" onRetry={() => refetch()} />
  if (!groups || groups.length === 0) {
    return (
      <EmptyState icon={<ActivityIcon size={38} />} title="No display groups">
        Create a display group to monitor its connected screens here.
      </EmptyState>
    )
  }

  // Rollup across all endpoints.
  let total = 0
  let online = 0
  for (const g of groups) {
    for (const ep of g.endpoints) {
      total++
      const b = g.browsers.find((x) => x.endpoint_id === ep.endpoint_id)
      const { status } = statusOf(b?.last_heartbeat, now)
      if (status === 'online' || status === 'idle') online++
    }
  }

  return (
    <>
      <div className={styles.statGrid} style={{ marginBottom: 'var(--s5)' }}>
        <Card className={styles.stat}>
          <div className={styles.statValue}>{total}</div>
          <div className={styles.statLabel}>Endpoints</div>
        </Card>
        <Card className={styles.stat}>
          <div className={styles.statValue} style={{ color: 'var(--color-success)' }}>{online}</div>
          <div className={styles.statLabel}>Online</div>
        </Card>
        <Card className={styles.stat}>
          <div className={styles.statValue} style={{ color: total - online ? 'var(--color-danger)' : undefined }}>
            {total - online}
          </div>
          <div className={styles.statLabel}>Offline</div>
        </Card>
      </div>

      {groups.map((g) => (
        <GroupHealth key={g.group_id} group={g} now={now} onPreview={setPreview} />
      ))}

      {preview && (
        <DisplayPreviewModal
          groupId={preview.groupId}
          endpointId={preview.endpointId}
          endpointName={preview.name}
          currentImage={preview.currentImage}
          settings={preview.settings}
          onClose={() => setPreview(null)}
        />
      )}
    </>
  )
}

// Memoized so opening/closing the preview modal (parent state) doesn't
// re-render every group's endpoint rows.
const GroupHealth = memo(function GroupHealth({
  group,
  now,
  onPreview,
}: {
  group: DisplayGroup
  now: number
  onPreview: (t: PreviewTarget) => void
}) {
  return (
    <div style={{ marginBottom: 'var(--s5)' }}>
      <h3 style={{ fontSize: 'var(--text-md)', marginBottom: 'var(--s2)' }}>
        {group.group_id}
        <span className={styles.scopeNote} style={{ marginLeft: 'var(--s3)' }}>
          {group.image_count} photos{group.paused ? ' · paused' : ''}{group.night_paused ? ' · night' : ''}
        </span>
      </h3>

      {group.endpoints.length === 0 ? (
        <div className={styles.scopeNote}>No endpoints connected.</div>
      ) : (
        <div className={styles.list}>
          {group.endpoints.map((ep) => {
            const b = group.browsers.find((x) => x.endpoint_id === ep.endpoint_id)
            const { status, ageS } = statusOf(b?.last_heartbeat, now)
            const uptime = b ? (now - new Date(b.connected_at).getTime()) / 1000 : null
            return (
              <Card key={ep.endpoint_id} className={styles.item}>
                <div className={styles.itemMain}>
                  <div className={styles.itemTitle}>
                    {ep.name || ep.endpoint_id}
                    <span className={cn(styles.statusPill, pillClass[status])}>{status}</span>
                  </div>
                  <div className={styles.itemSub}>
                    {ep.current_image ? ep.current_image.split('/').pop() : 'no image'}
                    {status !== 'offline' && ageS != null && ` · heartbeat ${Math.round(ageS)}s ago`}
                    {status === 'offline' && ep.last_seen && ` · last seen ${new Date(ep.last_seen).toLocaleString()}`}
                  </div>
                  <div className={styles.itemSub}>
                    load {b?.last_load_ms != null ? `${Math.round(b.last_load_ms)} ms` : '—'}
                    {' · '}cycle {b?.last_cycle_ms != null ? `${Math.round(b.last_cycle_ms)} ms` : '—'}
                    {' · '}up {fmtUptime(uptime)}
                  </div>
                </div>
                <div className={styles.itemActions}>
                  <Button
                    size="sm"
                    onClick={() =>
                      onPreview({
                        groupId: group.group_id,
                        endpointId: ep.endpoint_id,
                        name: ep.name,
                        currentImage: ep.current_image,
                        settings: group.settings,
                      })
                    }
                  >
                    Preview
                  </Button>
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
})
