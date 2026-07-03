import { useEffect, useRef, type ReactNode } from 'react'
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

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Modal({ title, subtitle, onClose, children, footer, footerSpread, width, role = 'dialog' }: Props) {
  const modalRef = useRef<HTMLDivElement>(null)

  // Keep the latest onClose in a ref so the focus-management effect below can
  // stay mount-only. If the effect depended on `onClose`, an unstable inline
  // `onClose={() => …}` from the parent (re-created on every render — and the
  // Displays tab re-renders constantly from the live admin WS) would tear the
  // effect down and re-run it, whose cleanup refocuses `previouslyFocused` and
  // steals focus from whatever's open inside — snapping native <select>
  // dropdowns shut mid-interaction.
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null

    // Move focus into the dialog unless something inside (e.g. an autoFocus
    // input) already claimed it.
    const modal = modalRef.current
    if (modal && !modal.contains(document.activeElement)) {
      const first = modal.querySelector<HTMLElement>(FOCUSABLE)
      ;(first ?? modal).focus()
    }

    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onCloseRef.current()
        return
      }
      // Contain Tab within the dialog — without this, keyboard focus escapes
      // into the (inert-looking but live) page behind the backdrop.
      if (e.key === 'Tab' && modalRef.current) {
        const focusables = Array.from(modalRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
          (el) => el.offsetParent !== null,
        )
        if (focusables.length === 0) return
        const first = focusables[0]
        const last = focusables[focusables.length - 1]
        const active = document.activeElement
        if (e.shiftKey && (active === first || !modalRef.current.contains(active))) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && (active === last || !modalRef.current.contains(active))) {
          e.preventDefault()
          first.focus()
        }
      }
    }

    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
      previouslyFocused?.focus?.()
    }
    // Mount-only: focus trap + focus restoration run exactly once each. onClose
    // is read through onCloseRef so its identity changing never re-runs this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className={styles.backdrop} onMouseDown={onClose}>
      <div
        ref={modalRef}
        className={styles.modal}
        style={width ? ({ ['--mw' as string]: `${width}px` }) : undefined}
        role={role}
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className={styles.head}>
          <div className={styles.heading}>
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
