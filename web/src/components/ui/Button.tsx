import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import styles from './Button.module.css'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'link'
type Size = 'sm' | 'md' | 'lg'

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  block?: boolean
  loading?: boolean
  children?: ReactNode
}

export function Button({
  variant = 'secondary',
  size = 'md',
  block,
  loading,
  disabled,
  className,
  children,
  ...rest
}: Props) {
  const variantClass = variant === 'secondary' ? undefined : styles[variant]
  return (
    <button
      className={cn(
        styles.btn,
        variantClass,
        size !== 'md' && styles[size],
        block && styles.block,
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <span className={styles.spinner} />}
      {children}
    </button>
  )
}
