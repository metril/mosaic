import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import styles from './Card.module.css'

interface Props extends HTMLAttributes<HTMLDivElement> {
  pad?: boolean
  hover?: boolean
  children: ReactNode
}

export function Card({ pad, hover, className, children, ...rest }: Props) {
  return (
    <div className={cn(styles.card, pad && styles.pad, hover && styles.hover, className)} {...rest}>
      {children}
    </div>
  )
}
