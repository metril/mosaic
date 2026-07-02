import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Tooltip } from './Tooltip'
import styles from './IconButton.module.css'

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Accessible label — required since the button is icon-only. Shown as a
   *  styled tooltip on hover/focus. */
  label: string
  size?: 'sm' | 'md'
  bordered?: boolean
  active?: boolean
  children: ReactNode
}

export function IconButton({
  label,
  size = 'md',
  bordered,
  active,
  className,
  children,
  ...rest
}: Props) {
  return (
    <Tooltip content={label}>
      <button
        type="button"
        aria-label={label}
        aria-pressed={active}
        className={cn(
          styles.btn,
          size === 'sm' && styles.sm,
          bordered && styles.bordered,
          active && styles.active,
          className,
        )}
        {...rest}
      >
        {children}
      </button>
    </Tooltip>
  )
}
