import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { DisplayEndpoint, DisplayGroupSettings } from '@/api/types'
import { ENDPOINT_OVERRIDE_KEYS } from '@/api/types'
import { updateEndpoint } from '@/api/admin'
import { GROUPS_KEY } from '@/hooks/useAdminWs'
import { Modal, Button, Select, Toggle, ToggleField, Input } from '@/components/ui'
import { POSITION_OPTS, CAPTION_FIELD_OPTS } from '@/lib/settingsDefaults'
import { cn } from '@/lib/cn'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import styles from '@/pages/Admin.module.css'
import formStyles from '@/components/ui/Form.module.css'

type Key = (typeof ENDPOINT_OVERRIDE_KEYS)[number]

const LABELS: Record<Key, string> = {
  layout: 'Layout',
  image_fit: 'Image fit',
  clock_position: 'Clock position',
  overlay_theme: 'Overlay theme',
  transition_effect: 'Transition effect',
  transition_duration_ms: 'Transition duration (ms)',
  ken_burns: 'Ken Burns',
  ken_burns_intensity: 'Ken Burns intensity (%)',
  ken_burns_duration: 'Ken Burns duration (s)',
  show_caption: 'Show caption',
  caption_fields: 'Caption fields',
  caption_position: 'Caption position',
  weather_position: 'Weather position',
}

const SELECTS: Partial<Record<Key, { value: string; label: string }[]>> = {
  layout: [
    { value: 'single', label: 'Single' },
    { value: 'splitview', label: 'Split view' },
    { value: 'splitview-landscape', label: 'Split (landscape)' },
  ],
  image_fit: [
    { value: 'contain', label: 'Contain' },
    { value: 'cover', label: 'Cover' },
  ],
  overlay_theme: [
    { value: 'fade', label: 'Fade' },
    { value: 'solid', label: 'Solid' },
    { value: 'bubble', label: 'Bubble' },
    { value: 'blur', label: 'Blur' },
  ],
  transition_effect: [
    { value: 'none', label: 'None' },
    { value: 'fade', label: 'Fade' },
    { value: 'crossfade', label: 'Crossfade' },
    { value: 'slide', label: 'Slide' },
    { value: 'dissolve', label: 'Dissolve' },
  ],
  clock_position: [...POSITION_OPTS],
  caption_position: [...POSITION_OPTS],
  weather_position: [...POSITION_OPTS],
}

const TOGGLES: Key[] = ['ken_burns', 'show_caption']
const NUMBERS: Key[] = ['transition_duration_ms', 'ken_burns_intensity', 'ken_burns_duration']

interface Props {
  groupId: string
  endpoint: DisplayEndpoint
  groupSettings: DisplayGroupSettings
  onClose: () => void
}

export function EndpointOverrideModal({ groupId, endpoint, groupSettings, onClose }: Props) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<Partial<DisplayGroupSettings>>({
    ...(endpoint.settings_override ?? {}),
  })

  const mutation = useMutation({
    mutationFn: (override: Partial<DisplayGroupSettings>) =>
      updateEndpoint(groupId, endpoint.endpoint_id, { settings_override: override }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: GROUPS_KEY })
      toast.success('Overrides saved')
      onClose()
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Save failed'),
  })

  function setKey(key: Key, value: unknown | undefined) {
    setDraft((prev) => {
      const next = { ...prev }
      if (value === undefined) delete next[key]
      else (next as Record<string, unknown>)[key] = value
      return next
    })
  }

  function enable(key: Key, on: boolean) {
    if (!on) setKey(key, undefined)
    else setKey(key, groupSettings[key]) // seed from group default
  }

  function control(key: Key) {
    const val = (draft as Record<string, unknown>)[key]
    if (SELECTS[key]) {
      return (
        <Select
          className={formStyles.controlMd}
          value={String(val ?? '')}
          onChange={(e) => setKey(key, e.target.value)}
        >
          {SELECTS[key]!.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      )
    }
    if (TOGGLES.includes(key)) {
      return <Toggle checked={Boolean(val)} onChange={(v) => setKey(key, v)} label={LABELS[key]} />
    }
    if (NUMBERS.includes(key)) {
      return (
        <Input
          type="number"
          className={formStyles.controlNum}
          value={val === undefined ? '' : Number(val)}
          onChange={(e) => setKey(key, e.target.value === '' ? 0 : Number(e.target.value))}
        />
      )
    }
    if (key === 'caption_fields') {
      const arr = (val as string[]) ?? []
      return (
        <div className={cn(formStyles.chips, styles.chipsEnd)}>
          {CAPTION_FIELD_OPTS.map((o) => (
            <button
              type="button"
              key={o.value}
              aria-pressed={arr.includes(o.value)}
              className={cn(formStyles.chip, arr.includes(o.value) && formStyles.chipOn)}
              onClick={() =>
                setKey(
                  key,
                  arr.includes(o.value) ? arr.filter((x) => x !== o.value) : [...arr, o.value],
                )
              }
            >
              {o.label}
            </button>
          ))}
        </div>
      )
    }
    return null
  }

  return (
    <Modal
      title={`Overrides · ${endpoint.name || endpoint.endpoint_id}`}
      subtitle="Override group settings just for this display. Off = inherit."
      onClose={onClose}
      width={560}
      footerSpread
      footer={
        <>
          <Button variant="ghost" onClick={() => setDraft({})}>
            Reset all
          </Button>
          <div className={styles.rowCluster}>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" loading={mutation.isPending} onClick={() => mutation.mutate(draft)}>
              Save overrides
            </Button>
          </div>
        </>
      }
    >
      {ENDPOINT_OVERRIDE_KEYS.map((key) => {
        const on = key in draft
        return (
          <div key={key} className={styles.endpointRow}>
            <div className={styles.grow}>
              <ToggleField checked={on} onChange={(v) => enable(key, v)}>
                <span className={styles.epName}>{LABELS[key]}</span>
              </ToggleField>
              {!on && <div className={styles.epMeta}>Inherits group default</div>}
            </div>
            {on && control(key)}
          </div>
        )
      })}
    </Modal>
  )
}
