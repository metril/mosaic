import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { ImmichPull, PullResult } from '@/api/types'
import { listPulls, triggerPull, cancelPull, deletePull, pullStatus } from '@/api/immich'
import { Button, Card, EmptyState, Loading, IconButton, CloudIcon, PlusIcon, PlayIcon, SparkleIcon, XIcon, EditIcon, TrashIcon, useConfirm, LoadError } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { cn } from '@/lib/cn'
import { ImmichPullModal } from './ImmichPullModal'
import styles from '@/pages/Admin.module.css'

export function ImmichTab() {
  const { data: pulls, isLoading, isError, refetch } = useQuery({ queryKey: ['pulls'], queryFn: listPulls })
  const [editing, setEditing] = useState<ImmichPull | null>(null)
  const [creating, setCreating] = useState(false)

  if (isLoading) return <Loading label="Loading pulls…" />
  if (isError) return <LoadError title="Couldn’t load Immich pulls" onRetry={() => refetch()} />

  return (
    <>
      <div className={styles.toolbar}>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <PlusIcon size={16} /> New pull
        </Button>
      </div>

      {!pulls || pulls.length === 0 ? (
        <EmptyState icon={<CloudIcon size={38} />} title="No Immich pulls configured">
          Connect an Immich server to pull albums and people into a folder, on demand or on a cron
          schedule.
        </EmptyState>
      ) : (
        <div className={styles.list}>
          {pulls.map((p) => (
            <PullCard key={p.name} pull={p} onEdit={() => setEditing(p)} />
          ))}
        </div>
      )}

      {creating && <ImmichPullModal pull={null} onClose={() => setCreating(false)} />}
      {editing && <ImmichPullModal pull={editing} onClose={() => setEditing(null)} />}
    </>
  )
}

function PullCard({ pull, onEdit }: { pull: ImmichPull; onEdit: () => void }) {
  const qc = useQueryClient()
  const [confirm, confirmModal] = useConfirm()
  // Poll while the server reports a run in progress, plus a short window right
  // after triggering so we catch the run starting. Derived from query data —
  // no mirrored "running" state (which used to setState during render).
  const [triggeredAt, setTriggeredAt] = useState(0)
  const status = useQuery({
    queryKey: ['pull-status', pull.name],
    queryFn: () => pullStatus(pull.name),
    refetchInterval: (query) => {
      const s = (query.state.data as PullResult | undefined)?.status
      if (s === 'running') return 1200
      return Date.now() - triggeredAt < 10_000 ? 1200 : false
    },
  })
  const result = status.data as PullResult | undefined
  const isRunning = result?.status === 'running'

  const trigger = useMutation({
    mutationFn: (delta: boolean) => triggerPull(pull.name, delta),
    onSuccess: (_r, delta) => {
      setTriggeredAt(Date.now())
      qc.invalidateQueries({ queryKey: ['pull-status', pull.name] })
      toast.info(`${delta ? 'Delta pull' : 'Pull'} “${pull.name}” started`)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Trigger failed'),
  })
  const cancel = useMutation({
    mutationFn: () => cancelPull(pull.name),
    onSuccess: () => toast.info('Pull cancelled'),
  })
  const del = useMutation({
    mutationFn: () => deletePull(pull.name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pulls'] })
      toast.success(`Deleted “${pull.name}”`)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Delete failed'),
  })

  const st = result?.status ?? 'idle'
  const pct =
    result && result.total > 0 ? Math.round((result.downloaded / result.total) * 100) : 0

  const stClass =
    st === 'running'
      ? styles.stRunning
      : st === 'completed'
        ? styles.stCompleted
        : st === 'failed'
          ? styles.stFailed
          : styles.stIdle

  return (
    <Card className={styles.item}>
      <div className={styles.itemMain}>
        <div className={styles.itemTitle}>
          {pull.name}
          <span className={cn(styles.statusPill, stClass)}>{st}</span>
        </div>
        <div className={styles.itemSub}>
          → {pull.target_folder} · {pull.count} images
          {pull.schedule_description ? ` · ${pull.schedule_description}` : ' · manual'}
        </div>
        {isRunning && result && (
          <>
            <div className={styles.progress}>
              <div className={styles.progressBar} style={{ width: `${pct}%` }} />
            </div>
            <div className={styles.itemSub}>
              {result.phase} · {result.downloaded}/{result.total} downloaded
              {result.skipped ? ` · ${result.skipped} skipped` : ''}
            </div>
          </>
        )}
        {!isRunning && result && result.errors?.length > 0 && (
          <div className={styles.itemSub} style={{ color: 'var(--color-danger)' }}>
            {result.errors.length} error(s): {result.errors[0]}
          </div>
        )}
      </div>
      <div className={styles.itemActions}>
        {isRunning ? (
          <IconButton label="Cancel pull" bordered onClick={() => cancel.mutate()}>
            <XIcon size={16} />
          </IconButton>
        ) : (
          <>
            <IconButton label="Run full pull" bordered onClick={() => trigger.mutate(false)}>
              <PlayIcon size={15} />
            </IconButton>
            <IconButton
              label="Delta pull — fetch only assets added since the last run (no folder wipe)"
              bordered
              onClick={() => trigger.mutate(true)}
            >
              <SparkleIcon size={15} />
            </IconButton>
          </>
        )}
        <IconButton label="Edit" bordered onClick={onEdit}>
          <EditIcon size={15} />
        </IconButton>
        <IconButton
          label="Delete"
          bordered
          danger
          onClick={async () => {
            if (
              await confirm({
                title: `Delete pull “${pull.name}”?`,
                message: 'The pull configuration is removed. Images already downloaded to the folder are kept.',
                confirmLabel: 'Delete pull',
              })
            )
              del.mutate()
          }}
        >
          <TrashIcon size={15} />
        </IconButton>
      </div>
      {confirmModal}
    </Card>
  )
}
