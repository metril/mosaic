import type { ReactNode } from 'react'
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
