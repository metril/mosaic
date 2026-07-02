import type { ReactNode } from 'react'
import { Button } from './Button'
import styles from './States.module.css'

export function Spinner({ size }: { size?: number } = {}) {
  const style = size ? { width: size, height: size, borderWidth: Math.max(2, Math.round(size / 9)) } : undefined
  return <span className={styles.spinner} style={style} role="status" aria-label="Loading" />
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className={styles.center}>
      <Spinner />
      <span>{label}</span>
    </div>
  )
}

/** Failed-fetch state with a retry action — use wherever a query's isError
 *  would otherwise leave a spinner or a misleading empty state. */
export function LoadError({
  title = 'Couldn’t load',
  onRetry,
  children,
}: {
  title?: string
  onRetry?: () => void
  children?: ReactNode
}) {
  return (
    <div className={styles.empty} role="alert">
      <div className={styles.emptyTitle}>{title}</div>
      {children && <div className={styles.emptyBody}>{children}</div>}
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry} style={{ marginTop: 'var(--s3)' }}>
          Retry
        </Button>
      )}
    </div>
  )
}

export function EmptyState({
  icon,
  title,
  children,
}: {
  icon?: ReactNode
  title: string
  children?: ReactNode
}) {
  return (
    <div className={styles.empty}>
      {icon && <div className={styles.emptyIcon}>{icon}</div>}
      <div className={styles.emptyTitle}>{title}</div>
      {children && <div className={styles.emptyBody}>{children}</div>}
    </div>
  )
}
