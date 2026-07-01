import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { ApiKey, DisplayGroup } from '@/api/types'
import { listGroups } from '@/api/admin'
import { listKeys, revealKey } from '@/api/keys'
import { GROUPS_KEY } from '@/hooks/useAdminWs'
import { authorizesDisplay } from '@/lib/scopeAccess'
import { Modal, Button, Label, Select, Toggle, CopyIcon, CheckIcon, ExternalIcon, EmptyState, KeyIcon } from '@/components/ui'
import { toast } from '@/stores/toast'
import { copyText } from '@/lib/clipboard'
import styles from '@/pages/Admin.module.css'

const NO_KEY = '__none__'

interface Props {
  /** Group-first: the group is fixed; pick endpoint + an authorizing key. */
  group?: DisplayGroup
  /** Key-first: the key is fixed; pick a target the key authorizes. */
  apiKey?: ApiKey
  onClose: () => void
}

interface Target {
  groupId: string
  endpointId: string | null
  isPersistent: boolean
  label: string
}

function buildUrl(groupId: string, endpointId: string | null, secret: string | null): string {
  const u = new URL('/display', window.location.origin)
  u.searchParams.set('group', groupId)
  if (endpointId) u.searchParams.set('endpoint', endpointId)
  if (secret) u.searchParams.set('key', secret)
  return u.toString()
}

export function ShareDisplayModal({ group, apiKey, onClose }: Props) {
  return apiKey ? (
    <KeyFirst apiKey={apiKey} onClose={onClose} />
  ) : group ? (
    <GroupFirst group={group} onClose={onClose} />
  ) : null
}

/* ----------------------------------------------------------------------- */
/* Group-first: group fixed, pick endpoint + authorizing key               */
/* ----------------------------------------------------------------------- */

function GroupFirst({ group, onClose }: { group: DisplayGroup; onClose: () => void }) {
  const endpoints = group.endpoints ?? []
  const [endpointId, setEndpointId] = useState<string | null>(null)
  const [keyId, setKeyId] = useState<string>(NO_KEY)
  const [showAll, setShowAll] = useState(false)

  const { data: keys } = useQuery({ queryKey: ['keys'], queryFn: listKeys })

  const selectedEp = endpoints.find((e) => e.endpoint_id === endpointId) ?? null
  const epPersistent = selectedEp?.is_persistent ?? false

  // Partition keys by whether they authorize this exact target and are recoverable.
  const { usable, others } = useMemo(() => {
    const usable: ApiKey[] = []
    const others: { key: ApiKey; reason: string }[] = []
    for (const k of keys ?? []) {
      const ok = authorizesDisplay(k.scopes, group.group_id, endpointId, epPersistent)
      if (ok && k.recoverable) usable.push(k)
      else others.push({ key: k, reason: !ok ? 'no access to this target' : 'secret unavailable' })
    }
    return { usable, others }
  }, [keys, group.group_id, endpointId, epPersistent])

  // If the currently-selected key is no longer usable for the new target, reset.
  const keyStillUsable = keyId === NO_KEY || usable.some((k) => k.id === keyId)
  const effectiveKeyId = keyStillUsable ? keyId : NO_KEY

  const { data: secretData } = useQuery({
    queryKey: ['key-secret', effectiveKeyId],
    queryFn: () => revealKey(effectiveKeyId),
    enabled: effectiveKeyId !== NO_KEY,
  })
  const secret = effectiveKeyId === NO_KEY ? null : secretData?.key ?? null

  const url = buildUrl(group.group_id, endpointId, secret)

  return (
    <Modal title="Copy display link" subtitle={group.group_id} onClose={onClose} width={560}>
      <div className={styles.formGrid}>
        {endpoints.length > 0 && (
          <div className={styles.fieldGroup}>
            <Label>Display</Label>
            <Select value={endpointId ?? ''} onChange={(e) => setEndpointId(e.target.value || null)}>
              <option value="">Whole group (any display)</option>
              {endpoints.map((e) => (
                <option key={e.endpoint_id} value={e.endpoint_id}>
                  {e.name || e.endpoint_id}
                  {e.is_persistent ? '' : ' (anonymous)'}
                </option>
              ))}
            </Select>
          </div>
        )}

        <div className={styles.fieldGroup}>
          <Label>API key</Label>
          <Select
            value={keyStillUsable ? keyId : NO_KEY}
            onChange={(e) => {
              setKeyId(e.target.value)
              if (e.target.value === NO_KEY) setShowAll(false)
            }}
          >
            <option value={NO_KEY}>No key (open / unauthenticated)</option>
            {usable.map((k) => (
              <option key={k.id} value={k.id}>
                {k.name}
              </option>
            ))}
            {showAll &&
              others.map(({ key, reason }) => (
                <option key={key.id} value={key.id} disabled>
                  {key.name} — {reason}
                </option>
              ))}
          </Select>
          {others.length > 0 && (
            <label className={styles.shareToggle}>
              <Toggle checked={showAll} onChange={setShowAll} label="Show keys without access" />
              <span>Show keys that don’t grant access</span>
            </label>
          )}
          {effectiveKeyId !== NO_KEY && !secret && (
            <div className={styles.shareWarn}>Could not load this key’s secret.</div>
          )}
        </div>

        <UrlBlock url={url} />
      </div>
    </Modal>
  )
}

/* ----------------------------------------------------------------------- */
/* Key-first: key fixed, pick a target it authorizes                       */
/* ----------------------------------------------------------------------- */

function KeyFirst({ apiKey, onClose }: { apiKey: ApiKey; onClose: () => void }) {
  const { data: groups } = useQuery({ queryKey: GROUPS_KEY, queryFn: listGroups })

  const targets = useMemo<Target[]>(() => {
    const out: Target[] = []
    for (const g of groups ?? []) {
      if (authorizesDisplay(apiKey.scopes, g.group_id, null)) {
        out.push({ groupId: g.group_id, endpointId: null, isPersistent: false, label: `${g.group_id} · whole group` })
      }
      for (const ep of g.endpoints ?? []) {
        if (authorizesDisplay(apiKey.scopes, g.group_id, ep.endpoint_id, ep.is_persistent)) {
          out.push({
            groupId: g.group_id,
            endpointId: ep.endpoint_id,
            isPersistent: ep.is_persistent,
            label: `${g.group_id} · ${ep.name || ep.endpoint_id}`,
          })
        }
      }
    }
    return out
  }, [groups, apiKey.scopes])

  const [idx, setIdx] = useState(0)
  const target = targets[idx] ?? null

  const { data: secretData } = useQuery({
    queryKey: ['key-secret', apiKey.id],
    queryFn: () => revealKey(apiKey.id),
    enabled: apiKey.recoverable,
  })
  const secret = secretData?.key ?? null

  if (!apiKey.recoverable) {
    return (
      <Modal title="Copy display link" subtitle={apiKey.name} onClose={onClose} width={560}>
        <EmptyState icon={<KeyIcon size={36} />} title="Secret not recoverable">
          This key was migrated from the old store, so its secret can’t be embedded in a link. Rotate the key to enable
          display links.
        </EmptyState>
      </Modal>
    )
  }

  return (
    <Modal title="Copy display link" subtitle={apiKey.name} onClose={onClose} width={560}>
      <div className={styles.formGrid}>
        {targets.length === 0 ? (
          <EmptyState icon={<KeyIcon size={36} />} title="No reachable targets">
            This key’s scopes don’t grant access to any current display group or endpoint.
          </EmptyState>
        ) : (
          <>
            <div className={styles.fieldGroup}>
              <Label>Display target</Label>
              <Select value={String(idx)} onChange={(e) => setIdx(Number(e.target.value))}>
                {targets.map((t, i) => (
                  <option key={`${t.groupId}:${t.endpointId ?? ''}`} value={i}>
                    {t.label}
                  </option>
                ))}
              </Select>
            </div>
            {target && <UrlBlock url={buildUrl(target.groupId, target.endpointId, secret)} />}
          </>
        )}
      </div>
    </Modal>
  )
}

/* ----------------------------------------------------------------------- */

function UrlBlock({ url }: { url: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className={styles.fieldGroup}>
      <Label>Display URL</Label>
      <div className={styles.revealKey}>{url}</div>
      <div className={styles.shareActions}>
        <Button
          variant="primary"
          onClick={async () => {
            if (await copyText(url)) {
              setCopied(true)
              toast.success('Link copied')
            } else {
              toast.error('Copy failed — select and copy the link manually')
            }
          }}
        >
          {copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />} {copied ? 'Copied' : 'Copy link'}
        </Button>
        <Button variant="ghost" onClick={() => window.open(url, '_blank', 'noopener')}>
          <ExternalIcon size={15} /> Open
        </Button>
      </div>
    </div>
  )
}
