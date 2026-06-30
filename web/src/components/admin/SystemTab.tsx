import { useQuery, useMutation } from '@tanstack/react-query'
import { fetchMetrics, fetchHealth, testHomeAssistant } from '@/api/system'
import { Button, Card, Loading } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
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
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth })
  const metrics = useQuery({ queryKey: ['metrics'], queryFn: fetchMetrics, refetchInterval: 10_000 })

  const haTest = useMutation({
    mutationFn: testHomeAssistant,
    onSuccess: (r) => (r.success ? toast.success('Home Assistant reachable') : toast.error(r.error || 'HA unreachable')),
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'HA test failed'),
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--s5)' }}>
      <div>
        <h3 style={{ fontSize: 'var(--text-md)', marginBottom: 'var(--s3)' }}>Status</h3>
        <div className={styles.statGrid}>
          {stats.map((s) => (
            <Card key={s.label} className={styles.stat}>
              <div className={styles.statValue}>{s.value}</div>
              <div className={styles.statLabel}>{s.label}</div>
            </Card>
          ))}
        </div>
        <div className={styles.scopeNote} style={{ marginTop: 'var(--s2)' }}>
          Image directory: <span className={styles.mono}>{health.data?.image_directory}</span>
        </div>
      </div>

      <div>
        <h3 style={{ fontSize: 'var(--text-md)', marginBottom: 'var(--s3)' }}>Actions</h3>
        <div style={{ display: 'flex', gap: 'var(--s3)', flexWrap: 'wrap' }}>
          <Button loading={haTest.isPending} onClick={() => haTest.mutate()}>
            Test Home Assistant
          </Button>
          <a href="/docs" target="_blank" rel="noreferrer">
            <Button variant="ghost">API docs ↗</Button>
          </a>
        </div>
        <div className={styles.scopeNote} style={{ marginTop: 'var(--s2)' }}>
          Groups, endpoints and pulls now save automatically.
        </div>
      </div>
    </div>
  )
}
