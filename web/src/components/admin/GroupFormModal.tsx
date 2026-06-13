import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { DisplayGroup, DisplayGroupSettings } from '@/api/types'
import { createGroup, updateGroup } from '@/api/admin'
import { GROUPS_KEY } from '@/hooks/useAdminWs'
import { DEFAULT_SETTINGS } from '@/lib/settingsDefaults'
import { Modal, Button, Input, Label } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { GroupSettingsForm } from './GroupSettingsForm'

interface Props {
  /** Existing group to edit, or null to create. */
  group: DisplayGroup | null
  onClose: () => void
}

export function GroupFormModal({ group, onClose }: Props) {
  const qc = useQueryClient()
  const editing = group !== null
  const [groupId, setGroupId] = useState(group?.group_id ?? '')
  const [settings, setSettings] = useState<DisplayGroupSettings>(
    group ? { ...DEFAULT_SETTINGS, ...group.settings } : DEFAULT_SETTINGS,
  )

  const mutation = useMutation({
    mutationFn: () =>
      editing ? updateGroup(group!.group_id, settings) : createGroup({ group_id: groupId, ...settings }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: GROUPS_KEY })
      toast.success(editing ? 'Group updated' : `Group “${groupId}” created`)
      onClose()
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Save failed'),
  })

  const idValid = /^[a-zA-Z0-9_-]{1,64}$/.test(groupId)

  return (
    <Modal
      title={editing ? `Edit ${group!.group_id}` : 'New display group'}
      subtitle={editing ? undefined : 'A group syncs one or more displays.'}
      onClose={onClose}
      width={620}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={mutation.isPending}
            disabled={!editing && !idValid}
            onClick={() => mutation.mutate()}
          >
            {editing ? 'Save changes' : 'Create group'}
          </Button>
        </>
      }
    >
      {!editing && (
        <div style={{ marginBottom: 'var(--s4)' }}>
          <Label htmlFor="group-id">Group ID</Label>
          <Input
            id="group-id"
            value={groupId}
            onChange={(e) => setGroupId(e.target.value)}
            placeholder="living-room"
            autoFocus
          />
          {groupId && !idValid && (
            <div style={{ color: 'var(--color-danger)', fontSize: 'var(--text-xs)', marginTop: 4 }}>
              Letters, numbers, dashes and underscores only (max 64).
            </div>
          )}
        </div>
      )}

      <GroupSettingsForm value={settings} onChange={setSettings} />
    </Modal>
  )
}
