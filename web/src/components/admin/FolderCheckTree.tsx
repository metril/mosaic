import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { FolderNode } from '@/api/types'
import { fetchFolders } from '@/api/browse'
import { visibleFolders, hasSubfolders, isFolder } from '@/lib/tree'
import { Loading, EmptyState, SearchInput, ChevronRightIcon, FolderIcon, Checkbox } from '@/components/ui'
import { cn } from '@/lib/cn'
import styles from './FolderCheckTree.module.css'

interface Props {
  /** Currently selected folder paths (relative to the library root). */
  selected: Set<string>
  /** Toggle a single folder path on/off. */
  onToggle: (path: string) => void
}

/** Every folder anywhere under `root`, depth-first (root itself excluded). */
function flattenFolders(root: FolderNode): FolderNode[] {
  const out: FolderNode[] = []
  const walk = (f: FolderNode) => {
    for (const c of f.children) {
      if (isFolder(c)) {
        out.push(c)
        walk(c)
      }
    }
  }
  walk(root)
  return out
}

/** All ancestor paths of `path` ("a/b/c" → "a", "a/b"). */
function ancestorsOf(path: string): string[] {
  const parts = path.split('/')
  const out: string[] = []
  for (let i = 1; i < parts.length; i++) out.push(parts.slice(0, i).join('/'))
  return out
}

/** Nested folder picker with a checkbox per folder, mirroring the Browse
 *  sidebar tree. A search box filters and auto-expands to deeply nested
 *  matches. Selection feeds a group's comma-separated `folders` string. */
export function FolderCheckTree({ selected, onToggle }: Props) {
  const { data, isLoading, isError } = useQuery({ queryKey: ['folders'], queryFn: fetchFolders })
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')

  function toggleExpand(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(path) ? next.delete(path) : next.add(path)
      return next
    })
  }

  if (isLoading) return <Loading label="Loading folders…" />
  if (isError || !data) return <EmptyState icon={<FolderIcon size={32} />} title="Couldn’t load folders" />

  const q = query.trim().toLowerCase()

  // When searching, surface matches with their ancestors auto-expanded.
  let activeExpanded = expanded
  let visiblePaths: Set<string> | null = null
  if (q) {
    const matches = flattenFolders(data.tree).filter(
      (n) => n.name.toLowerCase().includes(q) || n.path.toLowerCase().includes(q),
    )
    const visible = new Set<string>()
    const ancestors = new Set<string>()
    for (const n of matches) {
      visible.add(n.path)
      for (const a of ancestorsOf(n.path)) {
        ancestors.add(a)
        visible.add(a)
      }
    }
    activeExpanded = ancestors
    visiblePaths = visible
  }

  let rows = visibleFolders(data.tree, activeExpanded)
  if (visiblePaths) rows = rows.filter((r) => visiblePaths!.has(r.node.path))

  const totalFolders = data.total_folders

  return (
    <div>
      {totalFolders > 8 && (
        <div className={styles.search}>
          <SearchInput
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search folders…"
            aria-label="Search folders"
          />
        </div>
      )}
      <div className={styles.tree}>
        {rows.length === 0 ? (
          <div className={styles.empty}>{q ? 'No folders match.' : 'The library is a single flat folder.'}</div>
        ) : (
          rows.map(({ node, depth }) => {
            const expandable = hasSubfolders(node)
            const isOpen = activeExpanded.has(node.path)
            const checked = selected.has(node.path)
            return (
              <div
                key={node.path}
                className={cn(styles.row, checked && styles.rowChecked)}
                style={{ paddingLeft: `calc(${depth} * 14px + 0.5em)` }}
              >
                {expandable && !q ? (
                  <button
                    type="button"
                    className={cn(styles.twisty, isOpen && styles.twistyOpen)}
                    aria-label={isOpen ? 'Collapse' : 'Expand'}
                    onClick={() => toggleExpand(node.path)}
                  >
                    <ChevronRightIcon size={14} />
                  </button>
                ) : (
                  <span className={styles.twistySpacer} />
                )}
                <span className={styles.label}>
                  <Checkbox checked={checked} onChange={() => onToggle(node.path)}>
                    <span className={styles.name} title={node.path}>
                      {node.name}
                    </span>
                  </Checkbox>
                </span>
                <span className={styles.count}>{node.file_count}</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
