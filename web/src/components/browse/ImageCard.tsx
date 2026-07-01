import { useEffect, useMemo, useState } from 'react'
import type { FileNode } from '@/api/types'
import { thumbUrl } from '@/api/browse'
import { cn } from '@/lib/cn'
import { HeartIcon, EyeOffIcon } from '@/components/ui'
import styles from './Browse.module.css'

interface Props {
  file: FileNode
  favorite: boolean
  hidden: boolean
  onOpen: () => void
  onToggleFav: () => void
  onToggleHide: () => void
}

export function ImageCard({ file, favorite, hidden, onOpen, onToggleFav, onToggleHide }: Props) {
  const [loaded, setLoaded] = useState(false)
  // On touch devices there's no hover, so reveal the favorite/hide actions on a
  // first tap (a second tap opens the lightbox); they auto-hide on scroll or
  // after a moment so the grid stays clean. Desktop keeps hover-reveal.
  const isTouch = useMemo(
    () => typeof window !== 'undefined' && 'matchMedia' in window && window.matchMedia('(hover: none)').matches,
    [],
  )
  const [revealed, setRevealed] = useState(false)

  useEffect(() => {
    if (!revealed) return
    const t = window.setTimeout(() => setRevealed(false), 3500)
    const onScroll = () => setRevealed(false)
    window.addEventListener('scroll', onScroll, { passive: true, once: true })
    return () => {
      window.clearTimeout(t)
      window.removeEventListener('scroll', onScroll)
    }
  }, [revealed])

  function handleActivate() {
    // First tap on touch reveals actions instead of opening; then it opens.
    if (isTouch && !revealed) {
      setRevealed(true)
      return
    }
    onOpen()
  }

  return (
    <div
      className={cn(styles.card, hidden && styles.cardHidden, revealed && styles.cardRevealed)}
      onClick={handleActivate}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpen()
        }
      }}
    >
      <div className={styles.thumbWrap}>
        <img
          className={cn(styles.thumb, loaded && styles.thumbLoaded)}
          src={thumbUrl(file.path)}
          alt={file.name}
          loading="lazy"
          decoding="async"
          onLoad={() => setLoaded(true)}
        />

        {favorite && (
          <span className={styles.favBadge} aria-hidden="true">
            <HeartIcon size={15} filled />
          </span>
        )}
        {hidden && <span className={styles.hiddenBadge}>Hidden</span>}

        <div className={styles.actions}>
          <button
            className={cn(styles.curBtn, favorite && styles.curBtnFav)}
            aria-label={favorite ? 'Remove favorite' : 'Add favorite'}
            title={favorite ? 'Remove favorite' : 'Add favorite'}
            onClick={(e) => {
              e.stopPropagation()
              onToggleFav()
            }}
          >
            <HeartIcon size={16} filled={favorite} />
          </button>
          <button
            className={styles.curBtn}
            aria-label={hidden ? 'Unhide image' : 'Hide from slideshows'}
            title={hidden ? 'Unhide image' : 'Hide from slideshows'}
            onClick={(e) => {
              e.stopPropagation()
              onToggleHide()
            }}
          >
            <EyeOffIcon size={16} />
          </button>
        </div>
      </div>

      <div className={styles.meta}>
        <div className={styles.name} title={file.name}>
          {file.name}
        </div>
        <div className={styles.sub}>
          {file.dimensions !== 'Unknown' ? file.dimensions : file.size_formatted}
        </div>
      </div>
    </div>
  )
}
