import { useEffect, useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchFolders, fetchFiles } from '@/api/browse'
import type { FileFilter, FileSort, SortOrder } from '@/api/browse'
import { useCuration } from '@/hooks/useCuration'
import { cn } from '@/lib/cn'
import { Loading, EmptyState, SearchInput, Select, Button, Spinner, Segmented, ImageIcon, HeartIcon, EyeOffIcon } from '@/components/ui'
import { FolderTree } from '@/components/browse/FolderTree'
import { VirtualGrid } from '@/components/browse/VirtualGrid'
import { Lightbox } from '@/components/browse/Lightbox'
import styles from '@/components/browse/Browse.module.css'

const PAGE = 200

export default function Browse() {
  const qc = useQueryClient()
  const { data: folders, isLoading, isError } = useQuery({ queryKey: ['folders'], queryFn: fetchFolders })
  const root = folders?.tree ?? null
  const { favSet, hiddenSet, toggleFavorite, toggleHidden, bulkFavorite, bulkHidden } = useCuration()

  const [selected, setSelected] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<FileFilter>('all')
  const [sort, setSort] = useState<FileSort>('name')
  const [order, setOrder] = useState<SortOrder>('asc')
  const [lightbox, setLightbox] = useState<number | null>(null)
  const [selectMode, setSelectMode] = useState(false)
  const [sel, setSel] = useState<Set<string>>(new Set())

  // Debounce the search box so we don't refetch on every keystroke.
  const [debouncedQ, setDebouncedQ] = useState('')
  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedQ(query.trim()), 300)
    return () => window.clearTimeout(id)
  }, [query])

  const scope = { folder: selected, q: debouncedQ, filter, sort, order }

  const filesQuery = useInfiniteQuery({
    queryKey: ['files', scope],
    queryFn: ({ pageParam }) =>
      fetchFiles({ folder: selected, q: debouncedQ, filter, sort, order, offset: pageParam, limit: PAGE }),
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
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [selected, debouncedQ, filter, sort, order])

  // True while the user is mid-type (debounce pending) or a scope refetch is in
  // flight — drives a "Searching…" hint so results never look silently stale.
  const searching =
    query.trim() !== debouncedQ ||
    (filesQuery.isFetching && !filesQuery.isFetchingNextPage && !filesQuery.isPending)

  function toggleExpand(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(path) ? next.delete(path) : next.add(path)
      return next
    })
  }

  function toggleSelect(path: string) {
    setSel((prev) => {
      const next = new Set(prev)
      next.has(path) ? next.delete(path) : next.add(path)
      return next
    })
  }
  function exitSelectMode() {
    setSelectMode(false)
    setSel(new Set())
  }
  // Escape leaves select mode (unless the lightbox is open — it owns Escape).
  useEffect(() => {
    if (!selectMode) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && lightbox === null) exitSelectMode()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectMode, lightbox])

  async function bulkAct(kind: 'fav' | 'hide', value: boolean) {
    const paths = [...sel]
    if (kind === 'fav') await bulkFavorite(paths, value)
    else await bulkHidden(paths, value)
    if (filter !== 'all') qc.invalidateQueries({ queryKey: ['files'] })
    setSel(new Set())
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
          {searching && (
            <span className={styles.searching}>
              <Spinner size={13} /> Searching…
            </span>
          )}
        </div>

        <div className={styles.toolbar}>
          <div className={styles.search}>
            <SearchInput
              placeholder="Search photos…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <Segmented
            ariaLabel="Show"
            options={[
              { value: 'all', label: 'All' },
              { value: 'favorites', label: <><HeartIcon size={13} filled={filter === 'favorites'} /> Favorites</> },
              { value: 'hidden', label: <><EyeOffIcon size={13} /> Hidden</> },
            ]}
            value={filter}
            onChange={setFilter}
          />
          <Select
            aria-label="Sort by"
            className={styles.sortSelect}
            value={`${sort}-${order}`}
            onChange={(e) => {
              const [s, o] = e.target.value.split('-') as [FileSort, SortOrder]
              setSort(s)
              setOrder(o)
            }}
          >
            <option value="name-asc">Name A–Z</option>
            <option value="name-desc">Name Z–A</option>
            <option value="date-desc">Newest first</option>
            <option value="date-asc">Oldest first</option>
            <option value="size-desc">Largest first</option>
            <option value="size-asc">Smallest first</option>
          </Select>
          <Button
            size="sm"
            variant={selectMode ? 'primary' : 'secondary'}
            onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
          >
            {selectMode ? 'Done' : 'Select'}
          </Button>
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

          {selectMode && (
            <div className={styles.bulkBar}>
              <span className={styles.bulkCount}>
                {sel.size} selected
              </span>
              <Button size="sm" variant="secondary" disabled={sel.size === 0} onClick={() => bulkAct('fav', true)}>
                <HeartIcon size={14} filled /> Favorite
              </Button>
              <Button size="sm" variant="secondary" disabled={sel.size === 0} onClick={() => bulkAct('fav', false)}>
                Unfavorite
              </Button>
              {filter === 'hidden' ? (
                <Button size="sm" variant="secondary" disabled={sel.size === 0} onClick={() => bulkAct('hide', false)}>
                  <EyeOffIcon size={14} /> Unhide
                </Button>
              ) : (
                <Button size="sm" variant="secondary" disabled={sel.size === 0} onClick={() => bulkAct('hide', true)}>
                  <EyeOffIcon size={14} /> Hide
                </Button>
              )}
              <Button size="sm" variant="ghost" disabled={sel.size === 0} onClick={() => setSel(new Set())}>
                Clear
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setSel(new Set(files.map((f) => f.path)))}
              >
                Select all loaded
              </Button>
            </div>
          )}

          {filesQuery.isPending ? (
            <div className={styles.skelGrid} aria-label="Loading photos" aria-busy="true">
              {Array.from({ length: 12 }).map((_, i) => (
                <div key={i} className={styles.skelCard}>
                  <div className={styles.skelThumb} />
                  <div className={styles.skelLine} />
                </div>
              ))}
            </div>
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
              selectMode={selectMode}
              selectedSet={sel}
              onToggleSelect={toggleSelect}
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
