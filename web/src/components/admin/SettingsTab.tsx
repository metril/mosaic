import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { AppSettings } from '@/api/types'
import { fetchSettings, updateSettings, testHomeAssistant } from '@/api/system'
import { FormSection, Row, ToggleRow, NumberRow, TextRow, Input, Button, Loading } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import styles from '@/pages/Admin.module.css'

export function SettingsTab() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings })
  const [form, setForm] = useState<AppSettings | null>(null)
  const [token, setToken] = useState('') // write-only; blank = keep existing

  useEffect(() => {
    if (data) setForm(data)
  }, [data])

  const save = useMutation({
    mutationFn: () => {
      const { has_ha_token: _h, ...rest } = form as AppSettings
      const patch: Record<string, unknown> = { ...rest }
      if (token) patch.ha_token = token
      return updateSettings(patch)
    },
    onSuccess: (res) => {
      qc.setQueryData(['settings'], res)
      setToken('')
      toast.success('Settings saved')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Save failed'),
  })

  const haTest = useMutation({
    mutationFn: testHomeAssistant,
    onSuccess: (r) => (r.success ? toast.success('Home Assistant reachable') : toast.error(r.error || 'HA unreachable')),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'HA test failed'),
  })

  if (isLoading || !form) return <Loading label="Loading settings…" />

  function set<K extends keyof AppSettings>(key: K, v: AppSettings[K]) {
    setForm((f) => (f ? { ...f, [key]: v } : f))
  }

  return (
    <div>
      <div className={styles.settingsGrid}>
      <FormSection title="Integrations" defaultOpen>
        <ToggleRow
          label="Home Assistant"
          hint="Enable clock / weather / night-mode via HA"
          value={form.ha_enabled}
          onChange={(v) => set('ha_enabled', v)}
        />
        <TextRow
          label="HA URL"
          hint="e.g. http://homeassistant.local:8123"
          value={form.ha_url}
          onChange={(v) => set('ha_url', v)}
          placeholder="http://homeassistant.local:8123"
          stack
        />
        <Row
          label="HA token"
          hint={form.has_ha_token ? 'A token is set — leave blank to keep it' : 'No token set'}
          stack
        >
          <Input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={form.has_ha_token ? '•••••••• (unchanged)' : 'paste long-lived access token'}
            autoComplete="new-password"
          />
          <div className={styles.shareActions}>
            <Button size="sm" loading={haTest.isPending} onClick={() => haTest.mutate()}>
              Test connection
            </Button>
            <span className="faint" style={{ fontSize: 'var(--text-xs)', alignSelf: 'center' }}>
              Save first, then test.
            </span>
          </div>
        </Row>
        <NumberRow
          label="HA request timeout"
          value={form.ha_http_timeout}
          onChange={(v) => set('ha_http_timeout', v ?? 10)}
          min={1}
          max={120}
          unit="sec"
        />
        <NumberRow
          label="Weather cache"
          hint="How long HA weather is cached"
          value={form.ha_weather_cache_ttl}
          onChange={(v) => set('ha_weather_cache_ttl', v ?? 600)}
          min={0}
          max={86400}
          unit="sec"
        />
        <NumberRow
          label="Immich request timeout"
          value={form.immich_http_timeout}
          onChange={(v) => set('immich_http_timeout', v ?? 30)}
          min={1}
          max={600}
          unit="sec"
        />
        <NumberRow
          label="Max images per pull"
          value={form.max_immich_pull_count}
          onChange={(v) => set('max_immich_pull_count', v ?? 1000)}
          min={1}
          max={1000000}
        />
      </FormSection>

      <FormSection title="Display & limits">
        <NumberRow
          label="Preload lead time"
          hint="Send preload this many seconds before a switch"
          value={form.preload_lead_time}
          onChange={(v) => set('preload_lead_time', v ?? 5)}
          min={0}
          max={60}
          unit="sec"
        />
        <NumberRow
          label="Browser stale timeout"
          value={form.browser_stale_timeout}
          onChange={(v) => set('browser_stale_timeout', v ?? 60)}
          min={5}
          max={3600}
          unit="sec"
        />
        <NumberRow
          label="Cleanup interval"
          value={form.cleanup_interval}
          onChange={(v) => set('cleanup_interval', v ?? 30)}
          min={5}
          max={3600}
          unit="sec"
        />
        <TextRow
          label="Endpoint cleanup"
          hint="Remove idle anonymous endpoints after (e.g. 24h, 7d, 0=never)"
          value={form.endpoint_cleanup}
          onChange={(v) => set('endpoint_cleanup', v)}
          placeholder="24h"
        />
        <NumberRow
          label="Max browsers / group"
          value={form.max_browsers_per_group}
          onChange={(v) => set('max_browsers_per_group', v ?? 100)}
          min={1}
          max={1000}
        />
        <NumberRow
          label="Max groups"
          value={form.max_groups}
          onChange={(v) => set('max_groups', v ?? 50)}
          min={1}
          max={1000}
        />
      </FormSection>

      <FormSection title="General">
        <TextRow
          label="Base URL"
          hint="Public URL used to build image links; blank = auto-detect"
          value={form.base_url}
          onChange={(v) => set('base_url', v)}
          placeholder="https://screensaver.example.com"
          stack
        />
      </FormSection>
      </div>

      <div style={{ marginTop: 'var(--s5)', display: 'flex', justifyContent: 'flex-end' }}>
        <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>
          Save settings
        </Button>
      </div>
    </div>
  )
}
