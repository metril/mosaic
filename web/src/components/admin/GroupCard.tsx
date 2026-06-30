import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import type { DisplayGroup } from '@/api/types'
import { groupAction, deleteGroup, type PlaybackAction } from '@/api/admin'
import { thumbUrl } from '@/api/browse'
import { useCuration } from '@/hooks/useCuration'
import { useNow, secondsUntil } from '@/hooks/useNow'
import { Card, IconButton, Button } from '@/components/ui'
import {
  PlayIcon,
  PauseIcon,
  PrevIcon,
  NextIcon,
  SkipBackIcon,
  SkipForwardIcon,
  RefreshIcon,
  EditIcon,
  CopyIcon,
  TrashIcon,
  ExternalIcon,
  HeartIcon,
  EyeOffIcon,
  HistoryIcon,
} from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { cn } from '@/lib/cn'
import { EndpointsPanel } from './EndpointsPanel'
import { HistoryModal } from './HistoryModal'
import { ShareDisplayModal } from './ShareDisplayModal'
import styles from '@/pages/Admin.module.css'

interface Props {
  group: DisplayGroup
  onEdit: () => void
  onClone: () => void
}

export function GroupCard({ group, onEdit, onClone }: Props) {
  const { favSet, hiddenSet, toggleFavorite, toggleHidden } = useCuration()
  const [showHistory, setShowHistory] = useState(false)
  const [showShare, setShowShare] = useState(false)

  const action = useMutation({
    mutationFn: (a: PlaybackAction) => groupAction(group.group_id, a),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Action failed'),
  })

  const del = useMutation({
    mutationFn: () => deleteGroup(group.group_id),
    onSuccess: () => toast.success(`Deleted ${group.group_id}`),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Delete failed'),
  })

  const current = group.browsers.find((b) => b.current_image)?.current_image ?? null
  const live = group.browser_count > 0
  const now = useNow()
  const secs = group.paused ? null : secondsUntil(group.next_switch_at, now)
  const endpoints = group.endpoints ?? []
  // Default the history view to the endpoint currently on screen, else the first.
  const shownEndpointId = group.browsers.find((b) => b.current_image)?.endpoint_id as string | undefined
  const historyEndpointId = shownEndpointId ?? endpoints[0]?.endpoint_id ?? null

  return (
    <Card className={styles.group}>
      <div className={styles.groupHead}>
        <div>
          <div className={styles.groupName}>{group.group_id}</div>
          <div className={styles.groupMeta}>
            {group.image_count} photos · {group.browser_count} connected · cycle {group.current_cycle}
          </div>
        </div>
        <div className={styles.groupBadges}>
          {group.night_paused && <span className={cn(styles.badge, styles.badgeNight)}>Night</span>}
          {group.paused ? (
            <span className={cn(styles.badge, styles.badgePaused)}>Paused</span>
          ) : (
            live && <span className={cn(styles.badge, styles.badgeLive)}>Live</span>
          )}
        </div>
      </div>

      <div className={styles.groupBody}>
        {current && (
          <div className={styles.nowShowing}>
            <img className={styles.nowThumb} src={thumbUrl(current)} alt="" />
            <div className={styles.nowInfo}>
              <div className={styles.nowLabel}>Now showing</div>
              <div className={styles.nowName} title={current}>
                {current.split('/').pop()}
              </div>
            </div>
            <div className={styles.nowCurate}>
              <IconButton
                label={favSet.has(current) ? 'Remove favorite' : 'Favorite'}
                size="sm"
                active={favSet.has(current)}
                onClick={() => toggleFavorite(current)}
              >
                <HeartIcon size={15} filled={favSet.has(current)} />
              </IconButton>
              <IconButton
                label={hiddenSet.has(current) ? 'Unhide' : 'Hide'}
                size="sm"
                active={hiddenSet.has(current)}
                onClick={() => toggleHidden(current)}
              >
                <EyeOffIcon size={15} />
              </IconButton>
            </div>
          </div>
        )}

        <div className={styles.playback}>
          <IconButton label="First image" size="sm" onClick={() => action.mutate('rewind-all')}>
            <SkipBackIcon size={17} />
          </IconButton>
          <IconButton label="Previous" size="sm" onClick={() => action.mutate('previous')}>
            <PrevIcon size={17} />
          </IconButton>
          {group.paused ? (
            <IconButton label="Play" size="sm" bordered onClick={() => action.mutate('play')}>
              <PlayIcon size={16} />
            </IconButton>
          ) : (
            <IconButton label="Pause" size="sm" bordered onClick={() => action.mutate('pause')}>
              <PauseIcon size={16} />
            </IconButton>
          )}
          <IconButton label="Next" size="sm" onClick={() => action.mutate('next')}>
            <NextIcon size={17} />
          </IconButton>
          <IconButton label="Last image" size="sm" onClick={() => action.mutate('forward-all')}>
            <SkipForwardIcon size={17} />
          </IconButton>
          <IconButton label="Refresh image pool" size="sm" onClick={() => action.mutate('refresh')}>
            <RefreshIcon size={16} />
          </IconButton>
        </div>

        <div className={styles.countdown}>
          {group.paused ? (
            <span>Paused</span>
          ) : secs != null ? (
            <>
              Next image in <span className={styles.countdownValue}>{secs}s</span>
            </>
          ) : (
            <span className="faint">Idle</span>
          )}
        </div>

        {group.error && <div style={{ color: 'var(--color-danger)', fontSize: 'var(--text-xs)' }}>{group.error}</div>}
      </div>

      <EndpointsPanel group={group} />

      <div className={styles.groupFoot}>
        <Button className={styles.grow} size="sm" block onClick={() => setShowShare(true)}>
          <ExternalIcon size={15} /> Copy display link
        </Button>
        <IconButton
          label="Image history"
          size="sm"
          bordered
          disabled={!historyEndpointId}
          onClick={() => setShowHistory(true)}
        >
          <HistoryIcon size={15} />
        </IconButton>
        <IconButton label="Edit" size="sm" bordered onClick={onEdit}>
          <EditIcon size={15} />
        </IconButton>
        <IconButton label="Clone" size="sm" bordered onClick={onClone}>
          <CopyIcon size={15} />
        </IconButton>
        <IconButton
          label="Delete"
          size="sm"
          bordered
          onClick={() => {
            if (confirm(`Delete group “${group.group_id}”? This cannot be undone.`)) del.mutate()
          }}
        >
          <TrashIcon size={15} />
        </IconButton>
      </div>

      {showHistory && historyEndpointId && (
        <HistoryModal
          groupId={group.group_id}
          endpoints={endpoints}
          initialEndpointId={historyEndpointId}
          onClose={() => setShowHistory(false)}
        />
      )}

      {showShare && <ShareDisplayModal group={group} onClose={() => setShowShare(false)} />}
    </Card>
  )
}
