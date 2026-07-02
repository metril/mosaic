import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchLogs, logsDownloadUrl } from '@/api/system'
import { SearchInput, Button, DownloadIcon, ToggleField } from '@/components/ui'
import { cn } from '@/lib/cn'
import styles from './LogsPanel.module.css'
import formStyles from '@/components/ui/Form.module.css'

const LEVELS = ['ALL', 'INFO', 'WARNING', 'ERROR'] as const
const LABEL: Record<string, string> = { ALL: 'All', INFO: 'Info', WARNING: 'Warn', ERROR: 'Error' }

function fmtTs(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour12: false })
}

export function LogsPanel() {
  const [level, setLevel] = useState<string>('ALL')
  const [q, setQ] = useState('')
  const [tail, setTail] = useState(true)
  const consoleRef = useRef<HTMLDivElement>(null)

  const { data, isPending } = useQuery({
    queryKey: ['logs', level, q],
    queryFn: () => fetchLogs({ level, q }),
    refetchInterval: tail ? 3000 : false,
  })
  const records = data?.records ?? []

  // Keep the newest line in view while tailing.
  useEffect(() => {
    if (tail && consoleRef.current) consoleRef.current.scrollTop = consoleRef.current.scrollHeight
  }, [records, tail])

  return (
    <div>
      <div className={styles.controls}>
        <div className={styles.chips} role="group" aria-label="Log level">
          {LEVELS.map((l) => (
            <button
              key={l}
              className={cn(styles.chip, level === l && styles.chipActive)}
              onClick={() => setLevel(l)}
            >
              {LABEL[l]}
            </button>
          ))}
        </div>
        <div className={formStyles.controlMd}>
          <SearchInput placeholder="Filter logs…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <ToggleField checked={tail} onChange={setTail}>Live tail</ToggleField>
        <div className={styles.spacer} />
        <a href={logsDownloadUrl()} download style={{ textDecoration: 'none' }}>
          <Button variant="secondary" size="sm">
            <DownloadIcon size={15} /> Download
          </Button>
        </a>
      </div>

      <div className={styles.console} ref={consoleRef}>
        {records.length === 0 ? (
          <div className={styles.empty}>{isPending ? 'Loading logs…' : 'No log records match.'}</div>
        ) : (
          records.map((r) => (
            <div key={r.seq} className={cn(styles.row, styles[r.level.toLowerCase() as keyof typeof styles])}>
              <span className={styles.ts}>{fmtTs(r.ts)}</span>
              <span className={styles.lvl}>{r.level}</span>
              <span className={styles.name}>{r.logger}</span>
              <span className={styles.msg}>{r.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
