import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import styles from './Pill.module.css'

export type PillTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger'

/** The one status/count pill spec: 11px/700, 2px 8px, pill radius.
 *  Tones map to the soft semantic fills. */
export function Pill({
  tone = 'neutral',
  className,
  children,
}: {
  tone?: PillTone
  className?: string
  children: ReactNode
}) {
  return <span className={cn(styles.pill, styles[tone], className)}>{children}</span>
}
