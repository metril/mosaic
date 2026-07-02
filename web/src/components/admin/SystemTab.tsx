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
import { Button, Card, Row, Loading, RefreshIcon, useConfirm, Checkbox, LoadError } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { LogsPanel } from './LogsPanel'
import { StatCards } from './StatCards'
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
  const [dragging, setDragging] = useState(false)
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
  if (health.isError) return <LoadError title="Couldn’t load system status" onRetry={() => health.refetch()} />

  const gauges = metrics.data?.gauges ?? {}
  const counters = metrics.data?.counters ?? {}

  return (
    <div className={styles.tabStack}>
      <StatCards
        items={[
          { label: 'Images', value: health.data?.image_count ?? '—' },
          { label: 'Uptime', value: metrics.data ? fmtUptime(metrics.data.uptime_seconds) : '—' },
          { label: 'Active groups', value: gauges.active_groups ?? '—' },
          { label: 'Connected displays', value: gauges.active_browsers ?? '—' },
          { label: 'WS connections', value: counters.websocket_connections_total ?? '—' },
          { label: 'Groups created', value: counters.groups_created_total ?? '—' },
        ]}
      />

      <Card pad>
        <h3 className={styles.sectionTitle}>Maintenance</h3>
        <Row
          label="Refresh caches"
          hint="Re-scan images and clear cached thumbnails after changing files on disk directly."
        >
          <Button loading={refresh.isPending} onClick={() => refresh.mutate()}>
            <RefreshIcon size={15} /> Refresh
          </Button>
        </Row>
        <Row label="Home Assistant" hint="Check the configured HA connection.">
          <Button loading={haTest.isPending} onClick={() => haTest.mutate()}>
            Test connection
          </Button>
        </Row>
        <Row label="API reference" hint="Interactive OpenAPI documentation served by this instance.">
          <Button href="/docs" target="_blank" rel="noreferrer">
            Open docs ↗
          </Button>
        </Row>
      </Card>

      <Card
        pad
        className={dragging ? styles.dropActive : undefined}
        onDragOver={(e: React.DragEvent) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e: React.DragEvent) => {
          e.preventDefault()
          setDragging(false)
          const f = e.dataTransfer.files?.[0]
          if (f) onImportFile(f)
        }}
      >
        <h3 className={styles.sectionTitle}>Backup &amp; restore</h3>
        <Row
          label="Export configuration"
          hint="Groups, Immich pulls, settings and curation as one JSON file. Secrets stay encrypted."
        >
          <div className={styles.rowCluster}>
            <Checkbox checked={includeKeys} onChange={setIncludeKeys}>Include API keys</Checkbox>
            <Button loading={exporting} onClick={onExport}>
              Export
            </Button>
          </div>
        </Row>
        <Row
          label="Restore from backup"
          hint="Overwrites current config and reloads — or drop a JSON file anywhere on this card."
        >
          <Button loading={importMut.isPending} onClick={() => fileInput.current?.click()}>
            Import config…
          </Button>
        </Row>
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
      </Card>

      <Card pad>
        <h3 className={styles.sectionTitle}>Logs</h3>
        <LogsPanel />
      </Card>

      {confirmModal}
    </div>
  )
}
