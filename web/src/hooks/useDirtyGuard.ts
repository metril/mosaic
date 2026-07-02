import { useEffect, useRef } from 'react'

/** Guard a form against silent data loss.
 *
 *  - `guard(close)` wraps a close handler: when dirty, it asks before
 *    discarding (covers Cancel, Escape, and backdrop clicks in one place,
 *    since Modal routes them all through onClose).
 *  - While dirty, a beforeunload handler also warns on tab close/reload.
 */
export function useDirtyGuard(dirty: boolean) {
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty

  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  return function guard(close: () => void) {
    return () => {
      if (dirtyRef.current && !window.confirm('Discard unsaved changes?')) return
      close()
    }
  }
}
