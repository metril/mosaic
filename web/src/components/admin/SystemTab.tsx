import { useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchMetrics,
  fetchHealth,
  testHomeAssistant,
  refreshCaches,
  downloadConfigExport,
  importConfig,
} from '@/api/system'
import { Button, Card, Loading, Toggle, RefreshIcon, useConfirm } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { LogsPanel } from './LogsPanel'
import styles from '@/pages/Admin.module.css'

function fmtUptime(s: number) {
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  return `${m}m`
}

export function SystemTab() {
  const qc = useQueryClient()
  const [confirm, confirmModal] = useConfirm()
  const [includeKeys, setIncludeKeys] = useState(false)
  const [exporting, setExporting] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth })
  const metrics = useQuery({ queryKey: ['metrics'], queryFn: fetchMetrics, refetchInterval: 10_000 })

  async function onExport() {
    setExporting(true)
    try {
      await downloadConfigExport(includeKeys)
      toast.success('Config exported')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  const importMut = useMutation({
    mutationFn: (bundle: unknown) => importConfig(bundle),
    onSuccess: (r) => {
      const n = Object.values(r.restored).reduce((a, b) => a + b, 0)
      toast.success(`Restored ${n} item${n === 1 ? '' : 's'}`)
      if (r.warning) toast.error(r.warning)
      qc.invalidateQueries()
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Import failed'),
  })

  async function onImportFile(file: File) {
    let bundle: unknown
    try {
      bundle = JSON.parse(await file.text())
    } catch {
      toast.error('Not a valid config JSON file')
      return
    }
    const ok = await confirm({
      title: 'Import configuration?',
      message:
        'This overwrites all current groups, Immich pulls, settings and curation with the contents of the file. This cannot be undone.',
      confirmLabel: 'Overwrite & import',
    })
    if (ok) importMut.mutate(bundle)
  }

  const haTest = useMutation({
    mutationFn: testHomeAssistant,
    onSuccess: (r) => (r.success ? toast.success('Home Assistant reachable') : toast.error(r.error || 'HA unreachable')),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'HA test failed'),
  })

  const refresh = useMutation({
    mutationFn: () => refreshCaches(),
    onSuccess: (r) => {
      const n = Object.keys(r.groups_refreshed ?? {}).length
      toast.success(n ? `Caches cleared · ${n} group${n === 1 ? '' : 's'} refreshed` : 'Caches cleared')
      qc.invalidateQueries({ queryKey: ['groups'] })
      qc.invalidateQueries({ queryKey: ['metrics'] })
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Refresh failed'),
  })

  if (health.isLoading) return <Loading label="Loading system status…" />

  const gauges = metrics.data?.gauges ?? {}
  const counters = metrics.data?.counters ?? {}

  const stats: { label: string; value: string | number }[] = [
    { label: 'Images', value: health.data?.image_count ?? '—' },
    { label: 'Uptime', value: metrics.data ? fmtUptime(metrics.data.uptime_seconds) : '—' },
    { label: 'Active groups', value: gauges.active_groups ?? '—' },
    { label: 'Connected displays', value: gauges.active_browsers ?? '—' },
    { label: 'WS connections', value: counters.websocket_connections_total ?? '—' },
    { label: 'Groups created', value: counters.groups_created_total ?? '—' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--s4)' }}>
      <Card pad>
        <h3 className={styles.sectionTitle}>Status</h3>
        <div className={styles.statGrid}>
          {stats.map((s) => (
            <Card key={s.label} className={styles.stat}>
              <div className={styles.statValue}>{s.value}</div>
              <div className={styles.statLabel}>{s.label}</div>
            </Card>
          ))}
        </div>
        <div className={styles.scopeNote} style={{ marginTop: 'var(--s3)' }}>
          Image directory: <span className={styles.mono}>{health.data?.image_directory}</span>
        </div>
      </Card>

      <Card pad>
        <h3 className={styles.sectionTitle}>Actions</h3>
        <div style={{ display: 'flex', gap: 'var(--s3)', flexWrap: 'wrap' }}>
          <Button loading={haTest.isPending} onClick={() => haTest.mutate()}>
            Test Home Assistant
          </Button>
          <Button loading={refresh.isPending} onClick={() => refresh.mutate()}>
            <RefreshIcon size={15} /> Refresh caches
          </Button>
          <a href="/docs" target="_blank" rel="noreferrer">
            <Button variant="ghost">API docs ↗</Button>
          </a>
        </div>
        <div className={styles.scopeNote} style={{ marginTop: 'var(--s3)' }}>
          Groups, endpoints and pulls save automatically. “Refresh caches” re-scans images and
          clears cached thumbnails/pages — use it after changing files on disk directly.
        </div>
      </Card>

      <Card pad>
        <h3 className={styles.sectionTitle}>Backup &amp; restore</h3>
        <div style={{ display: 'flex', gap: 'var(--s3)', flexWrap: 'wrap', alignItems: 'center' }}>
          <Button loading={exporting} onClick={onExport}>
            Export config
          </Button>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5em', fontSize: 'var(--text-sm)' }}>
            <Toggle checked={includeKeys} onChange={setIncludeKeys} label="Include API keys" />
            Include API keys
          </label>
          <Button loading={importMut.isPending} onClick={() => fileInput.current?.click()}>
            Import config…
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            style={{ display: 'none' }}
            onChange={(e) => {
              const f = e.target.files?.[0]
              e.target.value = '' // allow re-selecting the same file
              if (f) onImportFile(f)
            }}
          />
        </div>
        <div className={styles.scopeNote} style={{ marginTop: 'var(--s3)' }}>
          Downloads all groups, Immich pulls, settings and curation as one JSON file. Secrets stay
          encrypted; import overwrites current config and reloads without a restart.
        </div>
      </Card>

      <Card pad>
        <h3 className={styles.sectionTitle}>Logs</h3>
        <LogsPanel />
      </Card>

      {confirmModal}
    </div>
  )
}
