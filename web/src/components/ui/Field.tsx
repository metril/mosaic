import type { InputHTMLAttributes, SelectHTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { SearchIcon } from './Icon'
import styles from './Field.module.css'

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(styles.control, className)} {...rest} />
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
