import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import styles from './Button.module.css'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'link'
type Size = 'sm' | 'md' | 'lg'

interface CommonProps {
  variant?: Variant
  size?: Size
  block?: boolean
  loading?: boolean
  children?: ReactNode
}

type ButtonProps = CommonProps & ButtonHTMLAttributes<HTMLButtonElement> & { href?: undefined }
/** With `href` the component renders a real <a> styled as a button, keeping
 *  link affordances (middle-click, copy address) without nesting interactives. */
type AnchorProps = CommonProps & AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }

export function Button(props: ButtonProps | AnchorProps) {
  const { variant = 'secondary', size = 'md', block, loading, className, children } = props
  const classes = cn(
    styles.btn,
    variant !== 'secondary' && styles[variant],
    size !== 'md' && styles[size],
    block && styles.block,
    className,
  )
  const content = (
    <>
      {loading && <span className={styles.spinner} />}
      {children}
    </>
  )

  if (props.href !== undefined) {
    const { variant: _v, size: _s, block: _b, loading: _l, className: _c, children: _ch, ...rest } = props
    return (
      <a className={classes} {...rest}>
        {content}
      </a>
    )
  }

  const { variant: _v, size: _s, block: _b, loading: _l, className: _c, children: _ch, disabled, ...rest } = props
  return (
    <button className={classes} disabled={disabled || loading} {...rest}>
      {content}
    </button>
  )
}
