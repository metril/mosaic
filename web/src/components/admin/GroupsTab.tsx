import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { DisplayGroup } from '@/api/types'
import { cloneGroup } from '@/api/admin'
import { GROUPS_KEY, useGroupIds, useGroup } from '@/hooks/useAdminWs'
import { Button, EmptyState, Loading, GridIcon, PlusIcon, Modal, Input, Label, LoadError } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { GroupCard } from './GroupCard'
import { GroupFormModal } from './GroupFormModal'
import styles from '@/pages/Admin.module.css'

export function GroupsTab() {
  // Subscribe to just the id LIST: this tab re-renders only when a group is
  // added/removed, not when a group's live state ticks. Each card subscribes to
  // its own group via useGroup(id), so one group's update re-renders one card.
  const { data: ids, isLoading, isError, refetch } = useGroupIds()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<DisplayGroup | null>(null)
  const [cloning, setCloning] = useState<DisplayGroup | null>(null)

  if (isLoading && !ids) return <Loading label="Loading groups…" />
  if (isError && !ids) return <LoadError title="Couldn’t load display groups" onRetry={() => refetch()} />

  return (
    <>
      <div className={styles.toolbar}>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <PlusIcon size={16} /> New group
        </Button>
      </div>

      {!ids || ids.length === 0 ? (
        <EmptyState icon={<GridIcon size={38} />} title="No display groups yet">
          Create a group, then open <code>/display?group=&lt;id&gt;</code> on any screen to join it.
        </EmptyState>
      ) : (
        <div className={styles.groupGrid}>
          {ids.map((id) => (
            <GroupCardContainer key={id} groupId={id} onEdit={setEditing} onClone={setCloning} />
          ))}
        </div>
      )}

      {creating && <GroupFormModal group={null} onClose={() => setCreating(false)} />}
      {editing && <GroupFormModal group={editing} onClose={() => setEditing(null)} />}
      {cloning && <CloneModal source={cloning} onClose={() => setCloning(null)} />}
    </>
  )
}

/** Per-group subscription boundary: re-renders (and thus re-renders its GroupCard)
 *  only when THIS group's object changes — so one group's live tick never touches
 *  the others' cards. Keeps GroupCard's `group` prop unchanged. */
function GroupCardContainer({
  groupId,
  onEdit,
  onClone,
}: {
  groupId: string
  onEdit: (g: DisplayGroup) => void
  onClone: (g: DisplayGroup) => void
}) {
  const group = useGroup(groupId)
  if (!group) return null
  return <GroupCard group={group} onEdit={() => onEdit(group)} onClone={() => onClone(group)} />
}

function CloneModal({ source, onClose }: { source: DisplayGroup; onClose: () => void }) {
  const qc = useQueryClient()
  const [id, setId] = useState(`${source.group_id}-copy`)
  const valid = /^[a-zA-Z0-9_-]{1,64}$/.test(id)
  const mutation = useMutation({
    mutationFn: () => cloneGroup(source.group_id, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: GROUPS_KEY })
      toast.success(`Cloned to “${id}”`)
      onClose()
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Clone failed'),
  })
  return (
    <Modal
      title={`Clone ${source.group_id}`}
      subtitle="Copies all settings to a new group."
      onClose={onClose}
      width={440}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!valid} loading={mutation.isPending} onClick={() => mutation.mutate()}>
            Clone
          </Button>
        </>
      }
    >
      <Label htmlFor="clone-id">New group ID</Label>
      <Input id="clone-id" value={id} onChange={(e) => setId(e.target.value)} autoFocus />
    </Modal>
  )
}
