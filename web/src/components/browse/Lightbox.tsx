import { useEffect } from 'react'
import type { FileNode } from '@/api/types'
import { imageUrl } from '@/api/browse'
import { cn } from '@/lib/cn'
import { HeartIcon, EyeOffIcon, XIcon, ArrowLeftIcon, ArrowRightIcon } from '@/components/ui'
import styles from './Browse.module.css'

interface Props {
  files: FileNode[]
  index: number
  favSet: Set<string>
  hiddenSet: Set<string>
  onClose: () => void
  onIndex: (i: number) => void
  onToggleFav: (path: string) => void
  onToggleHide: (path: string) => void
}

export function Lightbox({
  files,
  index,
  favSet,
  hiddenSet,
  onClose,
  onIndex,
  onToggleFav,
  onToggleHide,
}: Props) {
  const file = files[index]

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft') onIndex((index - 1 + files.length) % files.length)
      else if (e.key === 'ArrowRight') onIndex((index + 1) % files.length)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [index, files.length, onClose, onIndex])

  if (!file) return null
  const favorite = favSet.has(file.path)
  const hidden = hiddenSet.has(file.path)

  return (
    <div className={styles.lb} onClick={onClose} role="dialog" aria-modal="true" aria-label={file.name}>
      <div className={styles.lbBar} onClick={(e) => e.stopPropagation()}>
        <div className={styles.lbInfo}>
          <div className={styles.lbName}>{file.name}</div>
          <div className={styles.lbDims}>
            {file.dimensions !== 'Unknown' ? `${file.dimensions} · ` : ''}
            {file.size_formatted}
            {' · '}
            {index + 1} / {files.length}
          </div>
        </div>
        <div className={styles.lbActions}>
          <button
            className={cn(styles.lbBtn, favorite && styles.lbBtnFav)}
            onClick={() => onToggleFav(file.path)}
            aria-label={favorite ? 'Remove favorite' : 'Add favorite'}
          >
            <HeartIcon size={19} filled={favorite} />
          </button>
          <button
            className={styles.lbBtn}
            onClick={() => onToggleHide(file.path)}
            aria-label={hidden ? 'Unhide' : 'Hide from slideshows'}
            style={hidden ? { color: 'var(--accent-hi)' } : undefined}
          >
            <EyeOffIcon size={19} />
          </button>
          <button className={styles.lbBtn} onClick={onClose} aria-label="Close">
            <XIcon size={19} />
          </button>
        </div>
      </div>

      <div className={styles.lbStage} onClick={onClose}>
        {files.length > 1 && (
          <button
            className={cn(styles.lbNav, styles.lbPrev)}
            onClick={(e) => {
              e.stopPropagation()
              onIndex((index - 1 + files.length) % files.length)
            }}
            aria-label="Previous"
          >
            <ArrowLeftIcon size={22} />
          </button>
        )}
        <img
          className={styles.lbImg}
          src={imageUrl(file.path)}
          alt={file.name}
          onClick={(e) => e.stopPropagation()}
        />
        {files.length > 1 && (
          <button
            className={cn(styles.lbNav, styles.lbNext)}
            onClick={(e) => {
              e.stopPropagation()
              onIndex((index + 1) % files.length)
            }}
            aria-label="Next"
          >
            <ArrowRightIcon size={22} />
          </button>
        )}
      </div>
    </div>
  )
}
