import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchTree } from '@/api/browse'
import { collectFiles, directFiles, findFolder } from '@/lib/tree'
import { useCuration } from '@/hooks/useCuration'
import type { FileNode } from '@/api/types'
import { cn } from '@/lib/cn'
import { Loading, EmptyState, SearchInput, ImageIcon, HeartIcon, EyeOffIcon } from '@/components/ui'
import { FolderTree } from '@/components/browse/FolderTree'
import { ImageCard } from '@/components/browse/ImageCard'
import { Lightbox } from '@/components/browse/Lightbox'
import styles from '@/components/browse/Browse.module.css'

type Filter = 'all' | 'favorites' | 'hidden'

export default function Browse() {
  const { data, isLoading, isError } = useQuery({ queryKey: ['tree'], queryFn: fetchTree })
  const root = data?.tree ?? null
  const { favSet, hiddenSet, toggleFavorite, toggleHidden } = useCuration()

  const [selected, setSelected] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [lightbox, setLightbox] = useState<number | null>(null)

  const q = query.trim().toLowerCase()
  const libraryMode = q.length > 0 || filter !== 'all'

  const allFiles = useMemo(() => (root ? collectFiles(root) : []), [root])

  const currentFolder = useMemo(
    () => (root ? findFolder(root, selected) ?? root : null),
    [root, selected],
  )

  // The grid's working set: folder-scoped when just browsing, library-wide when
  // searching or filtering by curation.
  const files: FileNode[] = useMemo(() => {
    if (!root) return []
    let base = libraryMode ? allFiles : currentFolder ? directFiles(currentFolder) : []
    if (q) base = base.filter((f) => f.name.toLowerCase().includes(q))
    if (filter === 'favorites') base = base.filter((f) => favSet.has(f.path))
    else if (filter === 'hidden') base = base.filter((f) => hiddenSet.has(f.path))
    return base
  }, [root, libraryMode, allFiles, currentFolder, q, filter, favSet, hiddenSet])

  function toggleExpand(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(path) ? next.delete(path) : next.add(path)
      return next
    })
  }

  if (isLoading) return <Loading label="Reading the library…" />
  if (isError || !root) {
    return (
      <EmptyState icon={<ImageIcon size={40} />} title="Couldn’t load the library">
        The image directory may be unavailable. Check the server and try again.
      </EmptyState>
    )
  }

  const totalFiles = data?.total_files ?? root.file_count
  const totalFolders = data?.total_folders ?? root.folder_count
  const crumbs = selected ? selected.split('/') : []

  return (
    <>
      <div className={styles.head}>
        <div className={styles.heading}>
          <span className={styles.title}>Library</span>
          <span className={styles.stats}>
            {totalFolders} folders · {totalFiles} photos
          </span>
        </div>

        <div className={styles.toolbar}>
          <div className={styles.search}>
            <SearchInput
              placeholder="Search photos…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className={styles.filterChips} role="group" aria-label="Show">
            <button
              className={cn(styles.chip, filter === 'all' && styles.chipActive)}
              onClick={() => setFilter('all')}
            >
              All
            </button>
            <button
              className={cn(styles.chip, filter === 'favorites' && styles.chipActive, styles.fav)}
              onClick={() => setFilter('favorites')}
            >
              <HeartIcon size={13} filled={filter === 'favorites'} /> Favorites
            </button>
            <button
              className={cn(styles.chip, filter === 'hidden' && styles.chipActive)}
              onClick={() => setFilter('hidden')}
            >
              <EyeOffIcon size={13} /> Hidden
            </button>
          </div>
        </div>
      </div>

      <div className={styles.layout}>
        <FolderTree
          root={root}
          selected={selected}
          expanded={expanded}
          onSelect={(p) => {
            setSelected(p)
            setFilter('all')
            setQuery('')
          }}
          onToggle={toggleExpand}
        />

        <div className={styles.main}>
          {libraryMode ? (
            <div className={styles.crumbs}>
              <span className={styles.crumbCurrent}>
                {filter === 'favorites'
                  ? 'Favorites'
                  : filter === 'hidden'
                    ? 'Hidden'
                    : 'Search results'}
              </span>
              <span className="faint">· {files.length}</span>
            </div>
          ) : (
            <div className={styles.crumbs}>
              <button className={styles.crumb} onClick={() => setSelected('')}>
                All photos
              </button>
              {crumbs.map((seg, i) => {
                const path = crumbs.slice(0, i + 1).join('/')
                const last = i === crumbs.length - 1
                return (
                  <span key={path} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3em' }}>
                    <span className="faint">/</span>
                    <button
                      className={cn(styles.crumb, last && styles.crumbCurrent)}
                      onClick={() => setSelected(path)}
                    >
                      {seg}
                    </button>
                  </span>
                )
              })}
            </div>
          )}

          {files.length === 0 ? (
            <EmptyState
              icon={filter === 'favorites' ? <HeartIcon size={36} /> : <ImageIcon size={36} />}
              title={
                filter === 'favorites'
                  ? 'No favorites yet'
                  : filter === 'hidden'
                    ? 'Nothing hidden'
                    : q
                      ? 'No matches'
                      : 'No photos here'
              }
            >
              {filter === 'favorites'
                ? 'Tap the heart on any photo to keep it here.'
                : filter === 'hidden'
                  ? 'Hidden photos stay out of slideshows but remain browsable.'
                  : q
                    ? 'Try a different search.'
                    : 'This folder has no images. Pick another from the sidebar.'}
            </EmptyState>
          ) : (
            <div className={styles.grid}>
              {files.map((file, i) => (
                <ImageCard
                  key={file.path}
                  file={file}
                  favorite={favSet.has(file.path)}
                  hidden={hiddenSet.has(file.path)}
                  onOpen={() => setLightbox(i)}
                  onToggleFav={() => toggleFavorite(file.path)}
                  onToggleHide={() => toggleHidden(file.path)}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {lightbox !== null && files[lightbox] && (
        <Lightbox
          files={files}
          index={lightbox}
          favSet={favSet}
          hiddenSet={hiddenSet}
          onClose={() => setLightbox(null)}
          onIndex={setLightbox}
          onToggleFav={toggleFavorite}
          onToggleHide={toggleHidden}
        />
      )}
    </>
  )
}
