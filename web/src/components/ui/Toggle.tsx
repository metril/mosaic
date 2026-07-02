import type { ReactNode } from 'react'
import styles from './Toggle.module.css'

interface Props {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  label?: string
}

export function Toggle({ checked, onChange, disabled, label }: Props) {
  return (
    <label className={styles.toggle}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={label}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className={styles.track} />
      <span className={styles.thumb} />
    </label>
  )
}

/** Toggle with a visible clickable label — the standard inline "switch + text"
 *  field. The text IS the accessible name (no duplicated aria-label). */
export function ToggleField({
  checked,
  onChange,
  disabled,
  children,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  children: ReactNode
}) {
  return (
    <label className={styles.field}>
      <span className={styles.toggle}>
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className={styles.track} />
        <span className={styles.thumb} />
      </span>
      <span className={styles.fieldLabel}>{children}</span>
    </label>
  )
}

/** Styled checkbox with a visible clickable label, for dense multi-select
 *  lists (scopes, albums, folder trees) where a switch would be heavy. */
export function Checkbox({
  checked,
  onChange,
  disabled,
  children,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  children?: ReactNode
}) {
  return (
    <label className={styles.checkbox}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className={styles.box} aria-hidden>
        <svg viewBox="0 0 12 12" width="10" height="10">
          <path d="M2 6.5 4.8 9 10 3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      {children != null && <span className={styles.fieldLabel}>{children}</span>}
    </label>
  )
}
