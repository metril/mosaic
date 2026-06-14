import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { DisplayEndpoint } from '@/api/types'
import { fetchEndpointHistory, endpointJump, groupAction, listGroups } from '@/api/admin'
import { thumbUrl } from '@/api/browse'
import { useCuration } from '@/hooks/useCuration'
import { GROUPS_KEY } from '@/hooks/useAdminWs'
import { Modal, IconButton, Button, Loading, EmptyState, HeartIcon, EyeOffIcon, HistoryIcon, PlayIcon, PauseIcon } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { cn } from '@/lib/cn'
import styles from './HistoryModal.module.css'

interface Props {
  groupId: string
  endpoints: DisplayEndpoint[]
  initialEndpointId: string
  onClose: () => void
}

/** Per-display image history: previously shown images, newest first. Clicking a
 *  past image jumps the live display back to it. Favorite/hide per image. */
export function HistoryModal({ groupId, endpoints, initialEndpointId, onClose }: Props) {
  const qc = useQueryClient()
  const { favSet, hiddenSet, toggleFavorite, toggleHidden } = useCuration()
  const [epId, setEpId] = useState(initialEndpointId)

  const ep = endpoints.find((e) => e.endpoint_id === epId) ?? endpoints[0]
  // current_image comes from the live GROUPS_KEY cache (endpoints prop), so the
  // query key changes — and the list refreshes — whenever a new image is shown.
  const currentImage = ep?.current_image ?? null

  const { data, isPending } = useQuery({
    queryKey: ['endpoint-history', groupId, ep?.endpoint_id, currentImage],
    queryFn: () => fetchEndpointHistory(groupId, ep!.endpoint_id),
    enabled: !!ep,
  })

  const jump = useMutation({
    mutationFn: (image: string) => endpointJump(groupId, ep!.endpoint_id, image),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['endpoint-history', groupId, ep!.endpoint_id] })
      toast.success('Display jumped to image')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Could not jump'),
  })

  // Live pause state from the shared groups cache (kept current by the admin WS).
  // Pause to hold a jumped image and step through history manually, then resume.
  const { data: groups } = useQuery({ queryKey: GROUPS_KEY, queryFn: listGroups })
  const paused = groups?.find((g) => g.group_id === groupId)?.paused ?? false
  const toggleCycle = useMutation({
    mutationFn: () => groupAction(groupId, paused ? 'play' : 'pause'),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Could not change cycling'),
  })

  const past = data?.history ?? []
  // Images shown after the current one (present when you've jumped back). They're
  // preserved in memory, so show them above NOW instead of letting them vanish.
  const ahead = data?.forward ?? []
  const totalOther = (data?.history_count ?? 0) + (data?.forward_count ?? 0)
  const name = ep?.name || ep?.endpoint_id || ''

  function Thumb({ path, isCurrent }: { path: string; isCurrent: boolean }) {
    return (
      <div className={cn(styles.item, isCurrent && styles.itemCurrent)}>
        <button
          className={styles.thumbBtn}
          disabled={isCurrent || jump.isPending}
          title={isCurrent ? 'Currently showing' : `Jump display to ${path.split('/').pop()}`}
          onClick={() => !isCurrent && jump.mutate(path)}
        >
          <img className={styles.thumb} src={thumbUrl(path)} alt="" loading="lazy" decoding="async" />
          {isCurrent && <span className={styles.nowBadge}>Now</span>}
          {hiddenSet.has(path) && <span className={styles.hiddenBadge}>Hidden</span>}
        </button>
        <div className={styles.itemBar}>
          <span className={styles.itemName} title={path}>
            {path.split('/').pop()}
          </span>
          <IconButton
            label={favSet.has(path) ? 'Remove favorite' : 'Favorite'}
            size="sm"
            active={favSet.has(path)}
            onClick={() => toggleFavorite(path)}
          >
            <HeartIcon size={14} filled={favSet.has(path)} />
          </IconButton>
          <IconButton
            label={hiddenSet.has(path) ? 'Unhide' : 'Hide from slideshows'}
            size="sm"
            active={hiddenSet.has(path)}
            onClick={() => toggleHidden(path)}
          >
            <EyeOffIcon size={14} />
          </IconButton>
        </div>
      </div>
    )
  }

  return (
    <Modal
      title="Image history"
      subtitle={`${name} · ${totalOther} image${totalOther === 1 ? '' : 's'} shown this session`}
      onClose={onClose}
      width={780}
    >
      <div className={styles.toolbar}>
        <Button
          size="sm"
          variant={paused ? 'primary' : 'ghost'}
          loading={toggleCycle.isPending}
          onClick={() => toggleCycle.mutate()}
        >
          {paused ? <PlayIcon size={14} /> : <PauseIcon size={14} />}
          {paused ? 'Resume cycling' : 'Pause cycling'}
        </Button>
        <span className={styles.hint}>
          {paused
            ? 'Cycling paused — pick an image to hold it on the display.'
            : 'Pause cycling to hold the image you pick.'}
        </span>
      </div>

      {endpoints.length > 1 && (
        <div className={styles.selector} role="group" aria-label="Display">
          {endpoints.map((e) => (
            <button
              key={e.endpoint_id}
              className={cn(styles.selBtn, e.endpoint_id === ep?.endpoint_id && styles.selBtnActive)}
              onClick={() => setEpId(e.endpoint_id)}
            >
              {e.name || e.endpoint_id}
            </button>
          ))}
        </div>
      )}

      {isPending ? (
        <Loading label="Loading history…" />
      ) : !currentImage && past.length === 0 && ahead.length === 0 ? (
        <EmptyState icon={<HistoryIcon size={36} />} title="No history yet">
          Connect a display and let it cycle — images shown here can be revisited.
        </EmptyState>
      ) : (
        <div className={styles.grid}>
          {/* Newest first: images shown after current (jumped past), then NOW, then older. */}
          {ahead.map((p, i) => (
            <Thumb key={`f-${p}-${i}`} path={p} isCurrent={false} />
          ))}
          {currentImage && <Thumb path={currentImage} isCurrent />}
          {past.map((p, i) => (
            <Thumb key={`h-${p}-${i}`} path={p} isCurrent={false} />
          ))}
        </div>
      )}
    </Modal>
  )
}
