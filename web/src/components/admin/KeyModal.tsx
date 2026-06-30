import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ApiKey } from '@/api/types'
import { createKey, updateKey, availableResources } from '@/api/keys'
import { Modal, Button, Input, Label, Toggle, CopyIcon, CheckIcon } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { cn } from '@/lib/cn'
import styles from '@/pages/Admin.module.css'

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
        onClick={() => {
          navigator.clipboard?.writeText(secret)
          setCopied(true)
          toast.success('Copied to clipboard')
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
          onClick={() => {
            navigator.clipboard?.writeText(created)
            setCopied(true)
            toast.success('Copied to clipboard')
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
      onClose={onClose}
      width={560}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!name || scopes.length === 0} loading={save.isPending} onClick={() => save.mutate()}>
            {editing ? 'Save' : 'Create key'}
          </Button>
        </>
      }
    >
      <div className={styles.formGrid}>
        <div className={styles.fieldGroup}>
          <Label htmlFor="key-name">Name</Label>
          <Input id="key-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Living-room TV" autoFocus />
        </div>

        {editing && (
          <label className={styles.pickerItem} style={{ padding: 'var(--s2) 0' }}>
            <Toggle checked={enabled} onChange={setEnabled} label="Enabled" />
            <span style={{ fontSize: 'var(--text-sm)' }}>Enabled</span>
          </label>
        )}

        <div className={styles.fieldGroup}>
          <Label>Scopes</Label>
          <label className={styles.pickerItem} style={{ padding: 'var(--s2) 0' }}>
            <Toggle checked={full} onChange={(v) => setScopes(v ? ['*'] : ['admin:display:monitor'])} label="Full access" />
            <span style={{ fontSize: 'var(--text-sm)' }}>Full access (*)</span>
          </label>

          {!full && (
            <div className={styles.pickerList}>
              {ADMIN_SCOPES.map(([s, label]) => (
                <label key={s} className={styles.pickerItem}>
                  <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggleScope(s)} />
                  {label}
                  <span className={cn(styles.pickerCount, styles.mono)}>{s}</span>
                </label>
              ))}
              {(resources.data?.groups ?? []).map((g) => {
                const s = `group:${g.group_id}`
                return (
                  <label key={s} className={styles.pickerItem}>
                    <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggleScope(s)} />
                    Group: {g.group_id}
                    <span className={cn(styles.pickerCount, styles.mono)}>{s}</span>
                  </label>
                )
              })}
              {(resources.data?.folders ?? []).map((f) => {
                const s = `folder:${f}`
                return (
                  <label key={s} className={styles.pickerItem}>
                    <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggleScope(s)} />
                    Folder: {f}
                    <span className={cn(styles.pickerCount, styles.mono)}>{s}</span>
                  </label>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}
