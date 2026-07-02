import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ApiKey } from '@/api/types'
import { createKey, updateKey, availableResources } from '@/api/keys'
import { Modal, Button, Input, Label, CopyIcon, CheckIcon, ToggleField, Checkbox } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { cn } from '@/lib/cn'
import { copyText } from '@/lib/clipboard'
import styles from '@/pages/Admin.module.css'
import { useDirtyGuard } from '@/hooks/useDirtyGuard'

/** Show a key secret with a copy button (used for the post-rotate reveal and
 *  the keys-list "reveal secret" action). */
export function SecretRevealModal({
  title,
  subtitle,
  secret,
  onClose,
}: {
  title: string
  subtitle: string
  secret: string
  onClose: () => void
}) {
  const [copied, setCopied] = useState(false)
  return (
    <Modal
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      width={520}
      footer={
        <Button variant="primary" onClick={onClose}>
          Done
        </Button>
      }
    >
      <div className={styles.revealKey}>{secret}</div>
      <Button
        style={{ marginTop: 'var(--s3)' }}
        onClick={async () => {
          if (await copyText(secret)) {
            setCopied(true)
            toast.success('Copied to clipboard')
          } else {
            toast.error('Copy failed — select and copy the key manually')
          }
        }}
      >
        {copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />} {copied ? 'Copied' : 'Copy key'}
      </Button>
    </Modal>
  )
}

/** One-time reveal of a freshly rotated secret. */
export function RotatedKeyModal({ name, secret, onClose }: { name: string; secret: string; onClose: () => void }) {
  return (
    <SecretRevealModal
      title={`Key rotated · ${name}`}
      subtitle="The old secret no longer works. You can reveal this new secret again from the keys list."
      secret={secret}
      onClose={onClose}
    />
  )
}

const ADMIN_SCOPES = [
  ['admin:display:monitor', 'View display groups'],
  ['admin:display:manage', 'Create / edit groups'],
  ['admin:display:playback', 'Play / pause / skip'],
  ['admin:endpoints:monitor', 'View endpoints'],
  ['admin:endpoints:manage', 'Manage endpoints'],
  ['admin:immich:monitor', 'View Immich pulls'],
  ['admin:immich:manage', 'Manage Immich pulls'],
  ['admin:immich:trigger', 'Trigger pulls'],
  ['images:read', 'Read images (legacy)'],
] as const

export function KeyModal({ apiKey, onClose }: { apiKey: ApiKey | null; onClose: () => void }) {
  const qc = useQueryClient()
  const editing = apiKey !== null
  const resources = useQuery({ queryKey: ['key-resources'], queryFn: availableResources })

  const [name, setName] = useState(apiKey?.name ?? '')
  const [enabled, setEnabled] = useState(apiKey?.enabled ?? true)
  const [scopes, setScopes] = useState<string[]>(apiKey?.scopes ?? ['admin:display:monitor'])
  const [created, setCreated] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const full = scopes.includes('*')
  function toggleScope(s: string) {
    setScopes((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]))
  }

  const dirty = editing
    ? name !== apiKey!.name || enabled !== apiKey!.enabled || JSON.stringify(scopes) !== JSON.stringify(apiKey!.scopes)
    : name !== '' || JSON.stringify(scopes) !== JSON.stringify(['admin:display:monitor'])
  // The post-create reveal is not a form — never guard it.
  const guard = useDirtyGuard(dirty && !created)
  const requestClose = guard(onClose)

  const save = useMutation({
    mutationFn: async () => {
      if (editing) return updateKey(apiKey!.id, { name, enabled, scopes })
      return createKey({ name, scopes })
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['keys'] })
      if (editing) {
        toast.success('Key updated')
        onClose()
      } else {
        setCreated((res as { key: string }).key)
      }
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Save failed'),
  })

  // After creation: show the one-time key reveal.
  if (created) {
    return (
      <Modal
        title="API key created"
        subtitle="Stored encrypted — you can reveal it again later from the keys list."
        onClose={onClose}
        width={520}
        footer={
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        }
      >
        <div className={styles.revealKey}>{created}</div>
        <Button
          style={{ marginTop: 'var(--s3)' }}
          onClick={async () => {
            if (await copyText(created)) {
              setCopied(true)
              toast.success('Copied to clipboard')
            } else {
              toast.error('Copy failed — select and copy the key manually')
            }
          }}
        >
          {copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />} {copied ? 'Copied' : 'Copy key'}
        </Button>
      </Modal>
    )
  }

  return (
    <Modal
      title={editing ? `Edit key · ${apiKey!.name}` : 'New API key'}
      onClose={requestClose}
      width={560}
      footer={
        <>
          <Button variant="ghost" onClick={requestClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!name || scopes.length === 0} loading={save.isPending} onClick={() => save.mutate()}>
            {editing ? 'Save' : 'Create key'}
          </Button>
        </>
      }
    >
      <fieldset disabled={save.isPending} className="fieldsetReset">
      <div className={styles.formGrid}>
        <div className={styles.fieldGroup}>
          <Label htmlFor="key-name">Name</Label>
          <Input id="key-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Living-room TV" autoFocus />
        </div>

        {editing && (
          <div className={styles.pickerItem} style={{ padding: 'var(--s2) 0' }}>
            <ToggleField checked={enabled} onChange={setEnabled}>Enabled</ToggleField>
          </div>
        )}

        <div className={styles.fieldGroup}>
          <Label>Scopes</Label>
          <div className={styles.pickerItem} style={{ padding: 'var(--s2) 0' }}>
            <ToggleField checked={full} onChange={(v) => setScopes(v ? ['*'] : ['admin:display:monitor'])}>
              Full access (*)
            </ToggleField>
          </div>

          {!full && (
            <div className={styles.pickerList}>
              {ADMIN_SCOPES.map(([s, label]) => (
                <div key={s} className={styles.pickerItem}>
                  <Checkbox checked={scopes.includes(s)} onChange={() => toggleScope(s)}>
                    {label}
                  </Checkbox>
                  <span className={cn(styles.pickerCount, styles.mono)}>{s}</span>
                </div>
              ))}
              {(resources.data?.groups ?? []).map((g) => {
                const s = `group:${g.group_id}`
                return (
                  <div key={s} className={styles.pickerItem}>
                    <Checkbox checked={scopes.includes(s)} onChange={() => toggleScope(s)}>
                      Group: {g.group_id}
                    </Checkbox>
                    <span className={cn(styles.pickerCount, styles.mono)}>{s}</span>
                  </div>
                )
              })}
              {(resources.data?.folders ?? []).map((f) => {
                const s = `folder:${f}`
                return (
                  <div key={s} className={styles.pickerItem}>
                    <Checkbox checked={scopes.includes(s)} onChange={() => toggleScope(s)}>
                      Folder: {f}
                    </Checkbox>
                    <span className={cn(styles.pickerCount, styles.mono)}>{s}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
      </fieldset>
    </Modal>
  )
}
