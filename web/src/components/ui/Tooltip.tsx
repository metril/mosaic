import { cloneElement, isValidElement, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { InfoIcon } from './Icon'
import styles from './Tooltip.module.css'

const SHOW_DELAY = 150

// Focus-modality tracking: focus events only pop a tooltip when the user is
// navigating by keyboard. Programmatic focus (modals autofocusing a button on
// open) and mouse clicks never should.
let keyboardModality = false
if (typeof window !== 'undefined') {
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') keyboardModality = true
  }, true)
  window.addEventListener('pointerdown', () => {
    keyboardModality = false
  }, true)
}

/** Hover/focus tooltip rendered through a portal so it never clips inside
 *  modal or panel overflow. The trigger carries an aria-describedby pointing
 *  at an always-rendered visually-hidden copy; the floating bubble itself is
 *  purely visual. */
export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  const id = useId()
  const wrapRef = useRef<HTMLSpanElement>(null)
  const timer = useRef<number>()
  const [pos, setPos] = useState<{ top: number; left: number; below: boolean } | null>(null)

  function show() {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      const r = wrapRef.current?.getBoundingClientRect()
      if (!r) return
      // Flip below the trigger when there's no room above.
      const below = r.top < 64
      setPos({
        top: below ? r.bottom + 8 : r.top - 8,
        left: Math.min(Math.max(r.left + r.width / 2, 12), window.innerWidth - 12),
        below,
      })
    }, SHOW_DELAY)
  }

  function hide() {
    window.clearTimeout(timer.current)
    setPos(null)
  }

  // Attach the description to the real trigger (button/ⓘ), not the wrapper,
  // so screen readers announce it on focus.
  const trigger = isValidElement(children)
    ? cloneElement(children as React.ReactElement<{ 'aria-describedby'?: string }>, {
        'aria-describedby': id,
      })
    : children

  return (
    <span
      ref={wrapRef}
      className={styles.wrap}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={() => keyboardModality && show()}
      onBlur={hide}
      onKeyDown={(e) => e.key === 'Escape' && hide()}
    >
      {trigger}
      <span id={id} role="tooltip" className={styles.srOnly}>
        {content}
      </span>
      {pos &&
        createPortal(
          <span
            aria-hidden="true"
            className={styles.bubble}
            data-below={pos.below || undefined}
            style={{ top: pos.top, left: pos.left }}
          >
            {content}
          </span>,
          document.body,
        )}
    </span>
  )
}

/** Small ⓘ that reveals help text on hover or keyboard focus — the standard
 *  way to attach explanations to a label without visible helper text. */
export function InfoTip({ text }: { text: ReactNode }) {
  return (
    <Tooltip content={text}>
      <span className={styles.infoDot} tabIndex={0} aria-label="More information">
        <InfoIcon size={13} />
      </span>
    </Tooltip>
  )
}
