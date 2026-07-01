import type { InputHTMLAttributes, SelectHTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { SearchIcon } from './Icon'
import styles from './Field.module.css'

export function Input({
  className,
  invalid,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }) {
  return (
    <input
      className={cn(styles.control, invalid && styles.invalid, className)}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  )
}

/** Inline field validation message (danger-styled, with a leading marker). */
export function FieldError({ children }: { children: ReactNode }) {
  return (
    <div className={styles.fieldError} role="alert">
      <span aria-hidden="true">⚠</span>
      {children}
    </div>
  )
}

export function SearchInput({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className={styles.search}>
      <SearchIcon size={16} className={styles.searchIcon} />
      <input type="search" className={cn(styles.control, className)} {...rest} />
    </div>
  )
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  children: ReactNode
}
export function Select({ className, children, ...rest }: SelectProps) {
  return (
    <select className={cn(styles.control, className)} {...rest}>
      {children}
    </select>
  )
}

export function Label({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) {
  return (
    <label className={styles.label} htmlFor={htmlFor}>
      {children}
    </label>
  )
}
