import { useEffect, type ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { IconButton } from './IconButton'
import { XIcon } from './Icon'
import styles from './Modal.module.css'

interface Props {
  title: string
  subtitle?: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  footerSpread?: boolean
  width?: number
  /** Use "alertdialog" for confirmations that interrupt to demand a response. */
  role?: 'dialog' | 'alertdialog'
}

export function Modal({ title, subtitle, onClose, children, footer, footerSpread, width, role = 'dialog' }: Props) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [onClose])

  return (
    <div className={styles.backdrop} onMouseDown={onClose}>
      <div
        className={styles.modal}
        style={width ? ({ ['--mw' as string]: `${width}px` }) : undefined}
        role={role}
        aria-modal="true"
        aria-label={title}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className={styles.head}>
          <div>
            <div className={styles.title}>{title}</div>
            {subtitle && <div className={styles.subtitle}>{subtitle}</div>}
          </div>
          <IconButton label="Close" className={styles.close} onClick={onClose}>
            <XIcon size={18} />
          </IconButton>
        </div>
        <div className={styles.body}>{children}</div>
        {footer && <div className={cn(styles.foot, footerSpread && styles.footSpread)}>{footer}</div>}
      </div>
    </div>
  )
}
