import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { DisplayGroup } from '@/api/types'
import { groupAction, deleteGroup, type PlaybackAction } from '@/api/admin'
import { GROUPS_KEY } from '@/hooks/useAdminWs'
import { thumbUrl } from '@/api/browse'
import { useCuration } from '@/hooks/useCuration'
import { useNow, secondsUntil } from '@/hooks/useNow'
import { Card, IconButton, Button, useConfirm } from '@/components/ui'
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
  ExpandIcon,
  MegaphoneIcon,
  HeartIcon,
  EyeOffIcon,
  HistoryIcon,
} from '@/components/ui'
import { DisplayPreviewModal } from './DisplayPreviewModal'
import { AnnounceModal } from './AnnounceModal'
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
  const [confirm, confirmModal] = useConfirm()
  const [showHistory, setShowHistory] = useState(false)
  const [showShare, setShowShare] = useState(false)
  const [showPreview, setShowPreview] = useState(false)
  const [showAnnounce, setShowAnnounce] = useState(false)

  // The admin WS normally pushes the updated group state; invalidating on
  // settle is the fallback for reconnect gaps where that push never arrives.
  const qc = useQueryClient()
  const action = useMutation({
    mutationFn: (a: PlaybackAction) => groupAction(group.group_id, a),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Action failed'),
    onSettled: () => qc.invalidateQueries({ queryKey: GROUPS_KEY }),
  })

  const del = useMutation({
    mutationFn: () => deleteGroup(group.group_id),
    onSuccess: () => toast.success(`Deleted ${group.group_id}`),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Delete failed'),
    onSettled: () => qc.invalidateQueries({ queryKey: GROUPS_KEY }),
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
              <IconButton label="Preview display" size="sm" onClick={() => setShowPreview(true)}>
                <ExpandIcon size={15} />
              </IconButton>
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
        <IconButton label="Send announcement" size="sm" bordered onClick={() => setShowAnnounce(true)}>
          <MegaphoneIcon size={15} />
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
          onClick={async () => {
            if (
              await confirm({
                title: `Delete group “${group.group_id}”?`,
                message: 'This permanently removes the display group and its endpoints. This cannot be undone.',
                confirmLabel: 'Delete group',
              })
            )
              del.mutate()
          }}
        >
          <TrashIcon size={15} />
        </IconButton>
      </div>

      {confirmModal}

      {showHistory && historyEndpointId && (
        <HistoryModal
          groupId={group.group_id}
          endpoints={endpoints}
          initialEndpointId={historyEndpointId}
          onClose={() => setShowHistory(false)}
        />
      )}

      {showShare && <ShareDisplayModal group={group} onClose={() => setShowShare(false)} />}

      {showPreview && (
        <DisplayPreviewModal
          groupId={group.group_id}
          endpointId={shownEndpointId ?? historyEndpointId ?? ''}
          endpointName={null}
          currentImage={current}
          settings={group.settings}
          onClose={() => setShowPreview(false)}
        />
      )}

      {showAnnounce && <AnnounceModal groupId={group.group_id} onClose={() => setShowAnnounce(false)} />}
    </Card>
  )
}
