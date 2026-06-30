import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Input, Select } from './Field'
import { Toggle } from './Toggle'
import styles from './Form.module.css'

export function FormSection({
  title,
  icon,
  defaultOpen,
  children,
}: {
  title: string
  icon?: ReactNode
  defaultOpen?: boolean
  children: ReactNode
}) {
  return (
    <details className={styles.section} open={defaultOpen}>
      <summary>
        {icon && <span className={styles.sectionIcon}>{icon}</span>}
        {title}
      </summary>
      <div className={styles.sectionBody}>{children}</div>
    </details>
  )
}

export function SubHead({ children }: { children: ReactNode }) {
  return <div className={styles.subhead}>{children}</div>
}

/** A label/hint on the left, a control on the right. */
export function Row({
  label,
  hint,
  children,
  stack,
}: {
  label?: ReactNode
  hint?: ReactNode
  children: ReactNode
  stack?: boolean
}) {
  return (
    <div className={cn(styles.row, stack && styles.rowStack)}>
      {(label || hint) && (
        <div>
          {label && <div className={styles.rowLabel}>{label}</div>}
          {hint && <div className={styles.rowHint}>{hint}</div>}
        </div>
      )}
      <div className={cn(styles.rowControl, stack && styles.wide)}>{children}</div>
    </div>
  )
}

/* ---- Bound control helpers ------------------------------------------------ */

export function ToggleRow({
  label,
  hint,
  value,
  onChange,
}: {
  label: string
  hint?: string
  value: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <Row label={label} hint={hint}>
      <Toggle checked={value} onChange={onChange} label={label} />
    </Row>
  )
}

export function SelectRow<T extends string>({
  label,
  hint,
  value,
  onChange,
  options,
}: {
  label: string
  hint?: string
  value: T
  onChange: (v: T) => void
  options: { value: T; label: string }[]
}) {
  return (
    <Row label={label} hint={hint}>
      <Select
        className={styles.controlSm}
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    </Row>
  )
}

export function NumberRow({
  label,
  hint,
  value,
  onChange,
  min,
  max,
  step,
  unit,
  placeholder,
}: {
  label: string
  hint?: string
  value: number | null
  onChange: (v: number | null) => void
  min?: number
  max?: number
  step?: number
  unit?: string
  placeholder?: string
}) {
  return (
    <Row label={label} hint={hint}>
      <span className={styles.numUnit}>
        <Input
          type="number"
          style={{ width: 90 }}
          value={value ?? ''}
          min={min}
          max={max}
          step={step}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        />
        {unit && <span className={styles.unit}>{unit}</span>}
      </span>
    </Row>
  )
}

export function TextRow({
  label,
  hint,
  value,
  onChange,
  placeholder,
  stack,
}: {
  label: string
  hint?: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  stack?: boolean
}) {
  return (
    <Row label={label} hint={hint} stack={stack}>
      <Input
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        style={stack ? undefined : { width: 200 }}
      />
    </Row>
  )
}

export function ChipsRow<T extends string>({
  label,
  hint,
  value,
  onChange,
  options,
}: {
  label: string
  hint?: string
  value: T[]
  onChange: (v: T[]) => void
  options: { value: T; label: string }[]
}) {
  function toggle(v: T) {
    onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v])
  }
  return (
    <Row label={label} hint={hint} stack>
      <div className={styles.chips}>
        {options.map((o) => (
          <button
            type="button"
            key={o.value}
            className={cn(styles.chip, value.includes(o.value) && styles.chipOn)}
            onClick={() => toggle(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
    </Row>
  )
}
