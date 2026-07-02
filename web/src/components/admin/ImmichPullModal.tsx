import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ImmichPull } from '@/api/types'
import { savePull, testConnection, listAlbums, listPeople, type PullSave } from '@/api/immich'
import { Modal, Button, Input, SearchInput, Select, Label, CheckIcon, FieldError, ToggleField, Checkbox, InfoTip } from '@/components/ui'
import formStyles from '@/components/ui/Form.module.css'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { cn } from '@/lib/cn'
import styles from '@/pages/Admin.module.css'
import { useDirtyGuard } from '@/hooks/useDirtyGuard'

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

  const [initialForm] = useState(() => JSON.stringify(form))
  const guard = useDirtyGuard(JSON.stringify(form) !== initialForm)
  const requestClose = guard(onClose)

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
      onClose={requestClose}
      width={640}
      footer={
        <>
          <Button variant="ghost" onClick={requestClose}>
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
          <Label>
            API key {editing && <InfoTip text="Leave blank to keep the current key." />}
          </Label>
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
              <Input
                type="number"
                className={formStyles.controlNum}
                min={1}
                value={form.count}
                onChange={(e) => set('count', Number(e.target.value))}
              />
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
            <PickerList
              items={(albums.data ?? []).map((a) => ({ id: a.id, label: a.name, count: a.asset_count }))}
              selected={form.album_ids ?? []}
              onToggle={(id) => toggleId('album_ids', id)}
              onSetAll={(ids) => set('album_ids', ids)}
              searchPlaceholder="Filter albums…"
            />
          )}
          {loadDirectory && people.data && people.data.length > 0 && (
            <>
              <Label>People ({(form.person_ids ?? []).length} selected)</Label>
              <PickerList
                items={people.data.map((p) => ({ id: p.id, label: p.name || '(unnamed)' }))}
                selected={form.person_ids ?? []}
                onToggle={(id) => toggleId('person_ids', id)}
                onSetAll={(ids) => set('person_ids', ids)}
                searchPlaceholder="Filter people…"
              />
            </>
          )}

          <div className={styles.grid2}>
            <div>
              <Label>Taken after</Label>
              <Input
                type="date"
                className={formStyles.controlSm}
                value={form.taken_after ?? ''}
                onChange={(e) => set('taken_after', e.target.value || null)}
              />
            </div>
            <div>
              <Label>Taken before</Label>
              <Input
                type="date"
                className={formStyles.controlSm}
                value={form.taken_before ?? ''}
                onChange={(e) => set('taken_before', e.target.value || null)}
              />
            </div>
          </div>
          <div className={styles.grid2}>
            <div>
              <Label>Favorites</Label>
              <Select
                className={formStyles.controlMd}
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
              <Select
                className={formStyles.controlMd}
                value={form.media_type}
                onChange={(e) => set('media_type', e.target.value as 'IMAGE' | 'VIDEO')}
              >
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
                className={cn(styles.mono, formStyles.controlSm)}
              />
            </div>
            <div>
              <Label>Timezone</Label>
              <Input
                className={formStyles.controlSm}
                value={form.cron_timezone ?? ''}
                onChange={(e) => set('cron_timezone', e.target.value)}
                placeholder="UTC"
              />
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
    <div className={cn(styles.pickerItem)} style={{ padding: 'var(--s2) 0' }}>
      <ToggleField checked={value} onChange={onChange}>{label}</ToggleField>
      {value && <CheckIcon size={14} style={{ marginLeft: 'auto', color: 'var(--color-success)' }} />}
    </div>
  )
}

/** Searchable multi-select list with select-all/clear — large Immich servers
 *  have hundreds of albums/people, so a raw checkbox list doesn't scale. */
function PickerList({
  items,
  selected,
  onToggle,
  onSetAll,
  searchPlaceholder,
}: {
  items: { id: string; label: string; count?: number }[]
  selected: string[]
  onToggle: (id: string) => void
  onSetAll: (ids: string[]) => void
  searchPlaceholder: string
}) {
  const [q, setQ] = useState('')
  const visible = q
    ? items.filter((i) => i.label.toLowerCase().includes(q.toLowerCase()))
    : items
  const visibleIds = visible.map((i) => i.id)
  const allVisibleSelected = visible.length > 0 && visibleIds.every((id) => selected.includes(id))
  return (
    <div>
      {items.length > 8 && (
        <div style={{ display: 'flex', gap: 'var(--s2)', marginBottom: 'var(--s2)' }}>
          <SearchInput placeholder={searchPlaceholder} value={q} onChange={(e) => setQ(e.target.value)} />
          <Button
            size="sm"
            variant="secondary"
            onClick={() =>
              onSetAll(
                allVisibleSelected
                  ? selected.filter((id) => !visibleIds.includes(id))
                  : [...new Set([...selected, ...visibleIds])],
              )
            }
          >
            {allVisibleSelected ? 'Clear' : 'Select all'}
          </Button>
        </div>
      )}
      <div className={styles.pickerList}>
        {visible.length === 0 && <div className={styles.scopeNote}>No matches.</div>}
        {visible.map((item) => (
          <div key={item.id} className={styles.pickerItem}>
            <Checkbox checked={selected.includes(item.id)} onChange={() => onToggle(item.id)}>
              {item.label}
            </Checkbox>
            {item.count !== undefined && <span className={styles.pickerCount}>{item.count}</span>}
          </div>
        ))}
      </div>
    </div>
  )
}
