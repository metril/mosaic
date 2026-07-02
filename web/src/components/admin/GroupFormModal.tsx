import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { DisplayGroup, DisplayGroupSettings } from '@/api/types'
import { createGroup, updateGroup } from '@/api/admin'
import { GROUPS_KEY } from '@/hooks/useAdminWs'
import { DEFAULT_SETTINGS } from '@/lib/settingsDefaults'
import { Modal, Button, Input, Label, FieldError } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { GroupSettingsForm } from './GroupSettingsForm'
import { useDirtyGuard } from '@/hooks/useDirtyGuard'

interface Props {
  /** Existing group to edit, or null to create. */
  group: DisplayGroup | null
  onClose: () => void
}

export function GroupFormModal({ group, onClose }: Props) {
  const qc = useQueryClient()
  const editing = group !== null
  const [groupId, setGroupId] = useState(group?.group_id ?? '')
  const [initialSettings] = useState<DisplayGroupSettings>(
    group ? { ...DEFAULT_SETTINGS, ...group.settings } : DEFAULT_SETTINGS,
  )
  const [settings, setSettings] = useState<DisplayGroupSettings>(initialSettings)
  const dirty =
    JSON.stringify(settings) !== JSON.stringify(initialSettings) || (!editing && groupId !== '')
  const guard = useDirtyGuard(dirty)
  const requestClose = guard(onClose)

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
      onClose={requestClose}
      width={620}
      footer={
        <>
          <Button variant="ghost" onClick={requestClose}>
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
      <fieldset disabled={mutation.isPending} className="fieldsetReset">
        {!editing && (
          <div style={{ marginBottom: 'var(--s4)' }}>
            <Label htmlFor="group-id">Group ID</Label>
            <Input
              id="group-id"
              value={groupId}
              onChange={(e) => setGroupId(e.target.value)}
              placeholder="living-room"
              invalid={!!groupId && !idValid}
              autoFocus
            />
            {groupId && !idValid && (
              <FieldError>Letters, numbers, dashes and underscores only (max 64).</FieldError>
            )}
          </div>
        )}

        <GroupSettingsForm value={settings} onChange={setSettings} />
      </fieldset>
    </Modal>
  )
}
