import { useEffect, useState } from 'react'
import type { FileNode } from '@/api/types'
import { imageUrl, thumbUrl } from '@/api/browse'
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
  // The stat-only tree no longer ships dimensions; read them from the loaded
  // full image instead (free — it loads anyway). Keyed reset on index change.
  const [natural, setNatural] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  useEffect(() => {
    setNatural(null)
    setLoaded(false)
  }, [index])

  // Warm the neighbours so arrow navigation doesn't stare at a blank frame
  // while a brand-new full-res image downloads.
  useEffect(() => {
    if (files.length < 2) return
    const next = new Image()
    next.src = imageUrl(files[(index + 1) % files.length].path)
    const prev = new Image()
    prev.src = imageUrl(files[(index - 1 + files.length) % files.length].path)
  }, [index, files])

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
            {file.dimensions !== 'Unknown' ? `${file.dimensions} · ` : natural ? `${natural} · ` : ''}
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
        {/* Cached thumbnail as an instant placeholder under the full image. */}
        {!loaded && (
          <img
            className={styles.lbImg}
            src={thumbUrl(file.path)}
            alt=""
            aria-hidden
            style={{ position: 'absolute', filter: 'blur(14px)', opacity: 0.6 }}
          />
        )}
        <img
          className={styles.lbImg}
          src={imageUrl(file.path)}
          alt={file.name}
          onClick={(e) => e.stopPropagation()}
          onLoad={(e) => {
            setLoaded(true)
            setNatural(`${e.currentTarget.naturalWidth}×${e.currentTarget.naturalHeight}`)
          }}
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
