import { useEffect, useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchFolders, fetchFiles } from '@/api/browse'
import type { FileFilter } from '@/api/browse'
import { useCuration } from '@/hooks/useCuration'
import { cn } from '@/lib/cn'
import { Loading, EmptyState, SearchInput, ImageIcon, HeartIcon, EyeOffIcon } from '@/components/ui'
import { FolderTree } from '@/components/browse/FolderTree'
import { VirtualGrid } from '@/components/browse/VirtualGrid'
import { Lightbox } from '@/components/browse/Lightbox'
import styles from '@/components/browse/Browse.module.css'

const PAGE = 200

export default function Browse() {
  const qc = useQueryClient()
  const { data: folders, isLoading, isError } = useQuery({ queryKey: ['folders'], queryFn: fetchFolders })
  const root = folders?.tree ?? null
  const { favSet, hiddenSet, toggleFavorite, toggleHidden } = useCuration()

  const [selected, setSelected] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<FileFilter>('all')
  const [lightbox, setLightbox] = useState<number | null>(null)

  // Debounce the search box so we don't refetch on every keystroke.
  const [debouncedQ, setDebouncedQ] = useState('')
  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedQ(query.trim()), 300)
    return () => window.clearTimeout(id)
  }, [query])

  const scope = { folder: selected, q: debouncedQ, filter }

  const filesQuery = useInfiniteQuery({
    queryKey: ['files', scope],
    queryFn: ({ pageParam }) =>
      fetchFiles({ folder: selected, q: debouncedQ, filter, offset: pageParam, limit: PAGE }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => {
      const loaded = pages.reduce((n, p) => n + p.files.length, 0)
      return loaded < lastPage.total ? loaded : undefined
    },
    enabled: !!root,
  })

  const files = useMemo(
    () => filesQuery.data?.pages.flatMap((p) => p.files) ?? [],
    [filesQuery.data],
  )
  const total = filesQuery.data?.pages[0]?.total ?? 0

  // Switching scope should start the grid at the top.
  useEffect(() => {
    window.scrollTo({ top: 0 })
  }, [selected, debouncedQ, filter])

  function toggleExpand(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(path) ? next.delete(path) : next.add(path)
      return next
    })
  }

  // In a filtered view (Favorites/Hidden), a toggle changes membership, so the
  // server-filtered page must refetch. Badges update via the curation cache.
  function onToggleFav(path: string) {
    toggleFavorite(path)
    if (filter !== 'all') qc.invalidateQueries({ queryKey: ['files'] })
  }
  function onToggleHide(path: string) {
    toggleHidden(path)
    if (filter !== 'all') qc.invalidateQueries({ queryKey: ['files'] })
  }

  if (isLoading) return <Loading label="Reading the library…" />
  if (isError || !root) {
    return (
      <EmptyState icon={<ImageIcon size={40} />} title="Couldn’t load the library">
        The image directory may be unavailable. Check the server and try again.
      </EmptyState>
    )
  }

  const totalFiles = folders?.total_files ?? root.file_count
  const totalFolders = folders?.total_folders ?? root.folder_count
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
          onSelect={(p) => setSelected(p)}
          onToggle={toggleExpand}
        />

        <div className={styles.main}>
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
            <span className="faint">· {total}</span>
          </div>

          {filesQuery.isPending ? (
            <Loading label="Loading photos…" />
          ) : files.length === 0 ? (
            <EmptyState
              icon={filter === 'favorites' ? <HeartIcon size={36} /> : <ImageIcon size={36} />}
              title={
                filter === 'favorites'
                  ? 'No favorites yet'
                  : filter === 'hidden'
                    ? 'Nothing hidden'
                    : debouncedQ
                      ? 'No matches'
                      : 'No photos here'
              }
            >
              {filter === 'favorites'
                ? 'Tap the heart on any photo to keep it here.'
                : filter === 'hidden'
                  ? 'Hidden photos stay out of slideshows but remain browsable.'
                  : debouncedQ
                    ? 'Try a different search.'
                    : 'This folder has no images. Pick another from the sidebar.'}
            </EmptyState>
          ) : (
            <VirtualGrid
              files={files}
              favSet={favSet}
              hiddenSet={hiddenSet}
              onOpen={setLightbox}
              onToggleFav={onToggleFav}
              onToggleHide={onToggleHide}
              hasNextPage={filesQuery.hasNextPage}
              isFetchingNextPage={filesQuery.isFetchingNextPage}
              fetchNextPage={filesQuery.fetchNextPage}
            />
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
