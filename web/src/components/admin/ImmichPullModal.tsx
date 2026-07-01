import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ImmichPull } from '@/api/types'
import { savePull, testConnection, listAlbums, listPeople, type PullSave } from '@/api/immich'
import { Modal, Button, Input, Select, Label, Toggle, CheckIcon, FieldError } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { cn } from '@/lib/cn'
import styles from '@/pages/Admin.module.css'

interface Props {
  pull: ImmichPull | null
  onClose: () => void
}

export function ImmichPullModal({ pull, onClose }: Props) {
  const qc = useQueryClient()
  const editing = pull !== null

  const [form, setForm] = useState<PullSave>({
    name: pull?.name ?? '',
    server_url: pull?.server_url ?? '',
    api_key: '',
    target_folder: pull?.target_folder ?? '',
    count: pull?.count ?? 50,
    album_ids: pull?.album_ids ?? [],
    album_filter_mode: pull?.album_filter_mode ?? 'all',
    person_ids: pull?.person_ids ?? [],
    person_filter_mode: pull?.person_filter_mode ?? 'all',
    is_favorite: pull?.is_favorite ?? null,
    taken_after: pull?.taken_after ?? null,
    taken_before: pull?.taken_before ?? null,
    media_type: pull?.media_type ?? 'IMAGE',
    replace_existing: pull?.replace_existing ?? true,
    cron_expression: pull?.cron_expression ?? '',
    cron_timezone: pull?.cron_timezone ?? '',
    protect_favorites: pull?.protect_favorites ?? true,
    skip_excluded: pull?.skip_excluded ?? true,
  })

  function set<K extends keyof PullSave>(k: K, v: PullSave[K]) {
    setForm((f) => ({ ...f, [k]: v }))
  }

  // album/people pickers load on demand
  const connArgs = { server_url: form.server_url, api_key: form.api_key || undefined, pull_name: pull?.name }
  const [loadDirectory, setLoadDirectory] = useState(false)
  const albums = useQuery({
    queryKey: ['immich-albums', form.server_url, pull?.name],
    queryFn: () => listAlbums(connArgs),
    enabled: loadDirectory && !!form.server_url,
  })
  const people = useQuery({
    queryKey: ['immich-people', form.server_url, pull?.name],
    queryFn: () => listPeople(connArgs),
    enabled: loadDirectory && !!form.server_url,
  })

  const test = useMutation({
    mutationFn: () => testConnection(connArgs),
    onSuccess: (r) => {
      if (r.success) {
        toast.success(`Connected · ${r.album_count ?? 0} albums`)
        setLoadDirectory(true)
      } else toast.error(r.error || 'Connection failed')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Connection failed'),
  })

  const save = useMutation({
    mutationFn: () => {
      const body: PullSave = { ...form }
      if (editing && !body.api_key) delete body.api_key // preserve existing key
      if (!body.cron_expression) body.cron_expression = null
      if (!body.cron_timezone) body.cron_timezone = null
      return savePull(body)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pulls'] })
      toast.success(editing ? 'Pull updated' : `Pull “${form.name}” created`)
      onClose()
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Save failed'),
  })

  function toggleId(field: 'album_ids' | 'person_ids', id: string) {
    const arr = (form[field] as string[]) ?? []
    set(field, arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id])
  }

  const nameValid = /^[a-zA-Z0-9_-]{1,64}$/.test(form.name)
  const canSave = nameValid && form.server_url && form.target_folder && (editing || !!form.api_key)

  return (
    <Modal
      title={editing ? `Edit pull · ${pull!.name}` : 'New Immich pull'}
      onClose={onClose}
      width={640}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!canSave} loading={save.isPending} onClick={() => save.mutate()}>
            {editing ? 'Save pull' : 'Create pull'}
          </Button>
        </>
      }
    >
      <fieldset disabled={save.isPending} className="fieldsetReset">
      <div className={styles.formGrid}>
        <div className={styles.fieldGroup}>
          {!editing && (
            <>
              <Label htmlFor="pull-name">Name</Label>
              <Input
                id="pull-name"
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="vacation-2024"
                invalid={!!form.name && !nameValid}
              />
              {form.name && !nameValid && (
                <FieldError>Letters, numbers, dashes and underscores only (max 64).</FieldError>
              )}
            </>
          )}
          <Label>Server URL</Label>
          <Input value={form.server_url} onChange={(e) => set('server_url', e.target.value)} placeholder="https://immich.example.com" />
          <Label>API key {editing && <span className={styles.scopeNote}>(blank = keep current)</span>}</Label>
          <div style={{ display: 'flex', gap: 'var(--s2)' }}>
            <Input
              type="password"
              value={form.api_key ?? ''}
              onChange={(e) => set('api_key', e.target.value)}
              placeholder={editing ? '••••••••' : 'Immich API key'}
            />
            <Button onClick={() => test.mutate()} loading={test.isPending} disabled={!form.server_url}>
              Test &amp; load
            </Button>
          </div>
          <div className={styles.grid2}>
            <div>
              <Label>Target folder</Label>
              <Input value={form.target_folder} onChange={(e) => set('target_folder', e.target.value)} placeholder="immich/vacation" />
            </div>
            <div>
              <Label>Count</Label>
              <Input type="number" min={1} value={form.count} onChange={(e) => set('count', Number(e.target.value))} />
            </div>
          </div>
        </div>

        {/* Filters */}
        <div className={styles.fieldGroup}>
          <Label>Albums {albums.data && `(${(form.album_ids ?? []).length} selected)`}</Label>
          {!loadDirectory ? (
            <div className={styles.scopeNote}>Test &amp; load the server to pick albums and people.</div>
          ) : albums.isLoading ? (
            <div className={styles.scopeNote}>Loading albums…</div>
          ) : (
            <div className={styles.pickerList}>
              {(albums.data ?? []).map((a) => (
                <label key={a.id} className={styles.pickerItem}>
                  <input
                    type="checkbox"
                    checked={(form.album_ids ?? []).includes(a.id)}
                    onChange={() => toggleId('album_ids', a.id)}
                  />
                  {a.name}
                  <span className={styles.pickerCount}>{a.asset_count}</span>
                </label>
              ))}
            </div>
          )}
          {loadDirectory && people.data && people.data.length > 0 && (
            <>
              <Label>People ({(form.person_ids ?? []).length} selected)</Label>
              <div className={styles.pickerList}>
                {people.data.map((p) => (
                  <label key={p.id} className={styles.pickerItem}>
                    <input
                      type="checkbox"
                      checked={(form.person_ids ?? []).includes(p.id)}
                      onChange={() => toggleId('person_ids', p.id)}
                    />
                    {p.name || '(unnamed)'}
                  </label>
                ))}
              </div>
            </>
          )}

          <div className={styles.grid2}>
            <div>
              <Label>Taken after</Label>
              <Input type="date" value={form.taken_after ?? ''} onChange={(e) => set('taken_after', e.target.value || null)} />
            </div>
            <div>
              <Label>Taken before</Label>
              <Input type="date" value={form.taken_before ?? ''} onChange={(e) => set('taken_before', e.target.value || null)} />
            </div>
          </div>
          <div className={styles.grid2}>
            <div>
              <Label>Favorites</Label>
              <Select
                value={form.is_favorite === null ? 'any' : form.is_favorite ? 'fav' : 'nonfav'}
                onChange={(e) =>
                  set('is_favorite', e.target.value === 'any' ? null : e.target.value === 'fav')
                }
              >
                <option value="any">Any</option>
                <option value="fav">Favorites only</option>
                <option value="nonfav">Non-favorites</option>
              </Select>
            </div>
            <div>
              <Label>Media type</Label>
              <Select value={form.media_type} onChange={(e) => set('media_type', e.target.value as 'IMAGE' | 'VIDEO')}>
                <option value="IMAGE">Image</option>
                <option value="VIDEO">Video</option>
              </Select>
            </div>
          </div>
        </div>

        {/* Schedule + behavior */}
        <div className={styles.fieldGroup}>
          <div className={styles.grid2}>
            <div>
              <Label>Cron schedule</Label>
              <Input
                value={form.cron_expression ?? ''}
                onChange={(e) => set('cron_expression', e.target.value)}
                placeholder="0 3 * * *"
                className={styles.mono}
              />
            </div>
            <div>
              <Label>Timezone</Label>
              <Input value={form.cron_timezone ?? ''} onChange={(e) => set('cron_timezone', e.target.value)} placeholder="UTC" />
            </div>
          </div>
          <ToggleLine label="Replace existing on each pull" value={form.replace_existing!} onChange={(v) => set('replace_existing', v)} />
          <ToggleLine label="Protect favorited images from replacement" value={form.protect_favorites!} onChange={(v) => set('protect_favorites', v)} />
          <ToggleLine label="Skip images you’ve hidden" value={form.skip_excluded!} onChange={(v) => set('skip_excluded', v)} />
        </div>
      </div>
      </fieldset>
    </Modal>
  )
}

function ToggleLine({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className={cn(styles.pickerItem)} style={{ cursor: 'pointer', padding: 'var(--s2) 0' }}>
      <Toggle checked={value} onChange={onChange} label={label} />
      <span style={{ fontSize: 'var(--text-sm)' }}>{label}</span>
      {value && <CheckIcon size={14} style={{ marginLeft: 'auto', color: 'var(--color-success)' }} />}
    </label>
  )
}
