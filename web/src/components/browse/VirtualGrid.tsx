import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useWindowVirtualizer } from '@tanstack/react-virtual'
import type { FileNode } from '@/api/types'
import { ImageCard } from './ImageCard'

const MIN_COL = 170 // matches the old auto-fill minmax
const GAP = 16 // var(--s4)
const META_H = 60 // card meta strip below the square thumbnail

interface Props {
  files: FileNode[]
  favSet: Set<string>
  hiddenSet: Set<string>
  onOpen: (index: number) => void
  onToggleFav: (path: string) => void
  onToggleHide: (path: string) => void
}

/** Windowed photo grid: virtualizes rows against the window scroll so only the
 *  visible rows (plus a small overscan) ever mount. Thumbnails are therefore
 *  requested on demand — a folder with tens of thousands of images stays fast. */
export function VirtualGrid({
  files,
  favSet,
  hiddenSet,
  onOpen,
  onToggleFav,
  onToggleHide,
}: Props) {
  const parentRef = useRef<HTMLDivElement>(null)
  const [cols, setCols] = useState(1)
  const [estRow, setEstRow] = useState(240)
  const [scrollMargin, setScrollMargin] = useState(0)

  // Derive column count + an estimated row height from the container width.
  useLayoutEffect(() => {
    const el = parentRef.current
    if (!el) return
    const compute = () => {
      const width = el.clientWidth
      if (width <= 0) return
      const c = Math.max(1, Math.floor((width + GAP) / (MIN_COL + GAP)))
      const colWidth = (width - GAP * (c - 1)) / c
      setCols(c)
      setEstRow(Math.round(colWidth + META_H + GAP))
      setScrollMargin(el.getBoundingClientRect().top + window.scrollY)
    }
    compute()
    const ro = new ResizeObserver(compute)
    ro.observe(el)
    window.addEventListener('resize', compute)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', compute)
    }
  }, [])

  const rowCount = Math.ceil(files.length / cols)
  const virtualizer = useWindowVirtualizer({
    count: rowCount,
    estimateSize: () => estRow,
    overscan: 3,
    scrollMargin,
  })

  // Re-measure when the estimate (and thus row height) changes.
  useEffect(() => {
    virtualizer.measure()
  }, [estRow, cols, virtualizer])

  const rows = virtualizer.getVirtualItems()

  return (
    <div ref={parentRef} style={{ position: 'relative', height: virtualizer.getTotalSize() }}>
      {rows.map((row) => {
        const start = row.index * cols
        const rowFiles = files.slice(start, start + cols)
        return (
          <div
            key={row.key}
            data-index={row.index}
            ref={virtualizer.measureElement}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              transform: `translateY(${row.start - virtualizer.options.scrollMargin}px)`,
              display: 'grid',
              gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
              gap: GAP,
              paddingBottom: GAP,
            }}
          >
            {rowFiles.map((file, j) => {
              const index = start + j
              return (
                <ImageCard
                  key={file.path}
                  file={file}
                  favorite={favSet.has(file.path)}
                  hidden={hiddenSet.has(file.path)}
                  onOpen={() => onOpen(index)}
                  onToggleFav={() => onToggleFav(file.path)}
                  onToggleHide={() => onToggleHide(file.path)}
                />
              )
            })}
          </div>
        )
      })}
    </div>
  )
}
