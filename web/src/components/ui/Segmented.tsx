import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import styles from './Segmented.module.css'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
}

/** The one segmented control: a recessed track with a raised active segment.
 *  Replaces the per-file chip/selector variants in Logs, Browse and History. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  className,
}: {
  options: SegmentedOption<T>[]
  value: T
  onChange: (v: T) => void
  ariaLabel?: string
  className?: string
}) {
  return (
    <div className={cn(styles.track, className)} role="group" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          className={cn(styles.seg, o.value === value && styles.segOn)}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
