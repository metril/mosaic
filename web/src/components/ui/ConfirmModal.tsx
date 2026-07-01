import { useCallback, useState, type ReactNode } from 'react'
import { Modal } from './Modal'
import { Button } from './Button'

export interface ConfirmOptions {
  title: string
  /** Body text or nodes explaining the consequence. */
  message: ReactNode
  /** Label for the confirming action (default "Confirm"). */
  confirmLabel?: string
  cancelLabel?: string
  /** Style the confirm button as destructive (default true). */
  danger?: boolean
}

function ConfirmModal({
  opts,
  onResolve,
}: {
  opts: ConfirmOptions
  onResolve: (ok: boolean) => void
}) {
  const { title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = true } = opts
  return (
    <Modal
      title={title}
      role="alertdialog"
      onClose={() => onResolve(false)}
      width={440}
      footer={
        <>
          {/* autoFocus the safe action so Enter cancels, not confirms. */}
          <Button variant="ghost" autoFocus onClick={() => onResolve(false)}>
            {cancelLabel}
          </Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={() => onResolve(true)}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text)', lineHeight: 'var(--leading-normal)' }}>
        {message}
      </div>
    </Modal>
  )
}

/**
 * Promise-based confirmation. Replaces the browser `confirm()` with a styled,
 * accessible alertdialog. Usage:
 *
 *   const [confirm, confirmModal] = useConfirm()
 *   ...
 *   if (await confirm({ title, message })) doIt()
 *   ...
 *   return <>{...}{confirmModal}</>
 */
export function useConfirm() {
  const [state, setState] = useState<{ opts: ConfirmOptions; resolve: (ok: boolean) => void } | null>(null)

  const confirm = useCallback(
    (opts: ConfirmOptions) => new Promise<boolean>((resolve) => setState({ opts, resolve })),
    [],
  )

  const handle = useCallback(
    (ok: boolean) => {
      setState((s) => {
        s?.resolve(ok)
        return null
      })
    },
    [],
  )

  const element = state ? <ConfirmModal opts={state.opts} onResolve={handle} /> : null
  return [confirm, element] as const
}
