import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { DisplayGroup } from '@/api/types'
import { cloneGroup } from '@/api/admin'
import { GROUPS_KEY, useGroups } from '@/hooks/useAdminWs'
import { Button, EmptyState, Loading, GridIcon, PlusIcon, Modal, Input, Label } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { GroupCard } from './GroupCard'
import { GroupFormModal } from './GroupFormModal'
import styles from '@/pages/Admin.module.css'

export function GroupsTab() {
  const { data: groups, isLoading } = useGroups()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<DisplayGroup | null>(null)
  const [cloning, setCloning] = useState<DisplayGroup | null>(null)

  if (isLoading && !groups) return <Loading label="Loading groups…" />

  return (
    <>
      <div style={{ display: 'flex', marginBottom: 'var(--s4)' }}>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <PlusIcon size={16} /> New group
        </Button>
      </div>

      {!groups || groups.length === 0 ? (
        <EmptyState icon={<GridIcon size={38} />} title="No display groups yet">
          Create a group, then open <code>/display?group=&lt;id&gt;</code> on any screen to join it.
        </EmptyState>
      ) : (
        <div className={styles.groupGrid}>
          {groups.map((g) => (
            <GroupCard key={g.group_id} group={g} onEdit={() => setEditing(g)} onClone={() => setCloning(g)} />
          ))}
        </div>
      )}

      {creating && <GroupFormModal group={null} onClose={() => setCreating(false)} />}
      {editing && <GroupFormModal group={editing} onClose={() => setEditing(null)} />}
      {cloning && <CloneModal source={cloning} onClose={() => setCloning(null)} />}
    </>
  )
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
