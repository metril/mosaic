import { useToastStore } from '@/stores/toast'
import { cn } from '@/lib/cn'
import { XIcon } from './Icon'
import styles from './Toaster.module.css'

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts)
  const dismiss = useToastStore((s) => s.dismiss)

  return (
    <div className={styles.wrap} aria-live="polite" aria-atomic="false">
      {toasts.map((t) => (
        <div key={t.id} className={cn(styles.toast, styles[t.kind])} role={t.kind === 'error' ? 'alert' : 'status'}>
          <span className={styles.dot} />
          <span className={styles.msg}>{t.message}</span>
          <button className={styles.close} onClick={() => dismiss(t.id)} aria-label="Dismiss">
            <XIcon size={15} />
          </button>
        </div>
      ))}
    </div>
  )
}
