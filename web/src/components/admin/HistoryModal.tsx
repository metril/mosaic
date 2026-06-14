import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { DisplayEndpoint } from '@/api/types'
import { fetchEndpointHistory, endpointJump } from '@/api/admin'
import { thumbUrl } from '@/api/browse'
import { useCuration } from '@/hooks/useCuration'
import { Modal, IconButton, Loading, EmptyState, HeartIcon, EyeOffIcon, HistoryIcon } from '@/components/ui'
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

  const past = data?.history ?? []
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
      subtitle={`${name} · ${data?.history_count ?? 0} previously shown`}
      onClose={onClose}
      width={780}
    >
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
      ) : !currentImage && past.length === 0 ? (
        <EmptyState icon={<HistoryIcon size={36} />} title="No history yet">
          Connect a display and let it cycle — images shown here can be revisited.
        </EmptyState>
      ) : (
        <div className={styles.grid}>
          {currentImage && <Thumb path={currentImage} isCurrent />}
          {past.map((p, i) => (
            <Thumb key={`${p}-${i}`} path={p} isCurrent={false} />
          ))}
        </div>
      )}
    </Modal>
  )
}
