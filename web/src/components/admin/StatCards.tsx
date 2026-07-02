import type { ReactNode } from 'react'
import { Card } from '@/components/ui'
import { cn } from '@/lib/cn'
import styles from '@/pages/Admin.module.css'

export interface StatItem {
  label: string
  value: ReactNode
  /** Colors the value: 'ok' = success green, 'bad' = danger red. */
  tone?: 'ok' | 'bad'
}

const toneColor: Record<NonNullable<StatItem['tone']>, string> = {
  ok: 'var(--color-success)',
  bad: 'var(--color-danger)',
}

/** The stat-card row shared by the System and Health tabs. */
export function StatCards({ items, className }: { items: StatItem[]; className?: string }) {
  return (
    <div className={cn(styles.statGrid, className)}>
      {items.map((s) => (
        <Card key={s.label} className={styles.stat}>
          <div
            className={styles.statValue}
            style={s.tone ? { color: toneColor[s.tone] } : undefined}
          >
            {s.value}
          </div>
          <div className={styles.statLabel}>{s.label}</div>
        </Card>
      ))}
    </div>
  )
}
