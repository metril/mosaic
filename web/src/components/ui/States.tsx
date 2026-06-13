import type { ReactNode } from 'react'
import styles from './States.module.css'

export function Spinner() {
  return <span className={styles.spinner} role="status" aria-label="Loading" />
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
