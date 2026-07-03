import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { ApiKey } from '@/api/types'
import { listKeys, revokeKey, rotateKey, revealKey } from '@/api/keys'
import { Button, Card, EmptyState, Loading, IconButton, KeyIcon, PlusIcon, EditIcon, TrashIcon, RefreshIcon, ExternalIcon, UnlockIcon, useConfirm, LoadError } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { cn } from '@/lib/cn'
import { KeyModal, RotatedKeyModal, SecretRevealModal } from './KeyModal'
import { ShareDisplayModal } from './ShareDisplayModal'
import styles from '@/pages/Admin.module.css'

export function KeysTab() {
  const qc = useQueryClient()
  const [confirm, confirmModal] = useConfirm()
  const { data: keys, isLoading, isError, refetch } = useQuery({ queryKey: ['keys'], queryFn: listKeys })
  const [editing, setEditing] = useState<ApiKey | null>(null)
  const [creating, setCreating] = useState(false)
  const [sharing, setSharing] = useState<ApiKey | null>(null)
  const [rotated, setRotated] = useState<{ name: string; secret: string } | null>(null)
  const [revealed, setRevealed] = useState<{ name: string; secret: string } | null>(null)

  const reveal = useMutation({
    mutationFn: (k: ApiKey) => revealKey(k.id).then((r) => ({ name: k.name, key: r.key })),
    onSuccess: (res) => {
      if (res.key) setRevealed({ name: res.name, secret: res.key })
      else toast.error('Secret not recoverable — rotate the key')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Reveal failed'),
  })

  const revoke = useMutation({
    mutationFn: (id: string) => revokeKey(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['keys'] })
      toast.success('Key revoked')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Revoke failed'),
  })

  const rotate = useMutation({
    mutationFn: (k: ApiKey) => rotateKey(k.id).then((r) => ({ name: k.name, secret: r.key })),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['keys'] })
      setRotated(res)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Rotate failed'),
  })

  if (isLoading) return <Loading label="Loading keys…" />
  if (isError) return <LoadError title="Couldn’t load API keys" onRetry={() => refetch()} />

  return (
    <>
      <div className={styles.toolbar}>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <PlusIcon size={16} /> New API key
        </Button>
      </div>

      {!keys || keys.length === 0 ? (
        <EmptyState icon={<KeyIcon size={38} />} title="No API keys">
          Create a key to let displays or scripts authenticate without a login session.
        </EmptyState>
      ) : (
        <div className={styles.list}>
          {keys.map((k) => (
            <Card key={k.id} className={styles.item}>
              <div className={styles.itemMain}>
                <div className={styles.itemTitle}>
                  {k.name}
                  {!k.enabled && <span className={cn(styles.statusPill, styles.stIdle)}>disabled</span>}
                </div>
                <div className={cn(styles.itemSub, styles.mono)}>{k.key_prefix}</div>
                <div className={styles.itemSub}>
                  {k.scopes.length} scope{k.scopes.length !== 1 ? 's' : ''}
                  {k.last_used ? ` · last used ${new Date(k.last_used).toLocaleDateString()}` : ' · never used'}
                </div>
              </div>
              <div className={styles.itemActions}>
                <IconButton
                  label={k.recoverable ? 'Reveal / copy secret' : 'Secret not recoverable — rotate to enable'}
                  bordered
                  disabled={!k.recoverable || reveal.isPending}
                  onClick={() => reveal.mutate(k)}
                >
                  <UnlockIcon size={15} />
                </IconButton>
                <IconButton
                  label="Copy display link"
                  bordered
                  disabled={!k.enabled}
                  onClick={() => setSharing(k)}
                >
                  <ExternalIcon size={15} />
                </IconButton>
                <IconButton label="Edit" bordered onClick={() => setEditing(k)}>
                  <EditIcon size={15} />
                </IconButton>
                <IconButton
                  label="Rotate secret"
                  bordered
                  onClick={async () => {
                    if (
                      await confirm({
                        title: `Rotate “${k.name}”?`,
                        message: 'A new secret is generated and the current one stops working immediately. Update anything using the old key.',
                        confirmLabel: 'Rotate secret',
                        danger: false,
                      })
                    )
                      rotate.mutate(k)
                  }}
                >
                  <RefreshIcon size={15} />
                </IconButton>
                <IconButton
                  label="Revoke"
                  bordered
                  danger
                  onClick={async () => {
                    if (
                      await confirm({
                        title: `Revoke “${k.name}”?`,
                        message: 'The key is deleted and stops working immediately. This cannot be undone.',
                        confirmLabel: 'Revoke key',
                      })
                    )
                      revoke.mutate(k.id)
                  }}
                >
                  <TrashIcon size={15} />
                </IconButton>
              </div>
            </Card>
          ))}
        </div>
      )}

      {creating && <KeyModal apiKey={null} onClose={() => setCreating(false)} />}
      {editing && <KeyModal apiKey={editing} onClose={() => setEditing(null)} />}
      {sharing && <ShareDisplayModal apiKey={sharing} onClose={() => setSharing(null)} />}
      {rotated && (
        <RotatedKeyModal name={rotated.name} secret={rotated.secret} onClose={() => setRotated(null)} />
      )}
      {revealed && (
        <SecretRevealModal
          title={`API key · ${revealed.name}`}
          subtitle="Keep this secret safe. Anyone with it can access the authorized displays."
          secret={revealed.secret}
          onClose={() => setRevealed(null)}
        />
      )}
      {confirmModal}
    </>
  )
}
