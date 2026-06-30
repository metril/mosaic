import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchFolders } from '@/api/browse'
import { visibleFolders, hasSubfolders } from '@/lib/tree'
import { Loading, EmptyState, ChevronRightIcon, FolderIcon } from '@/components/ui'
import { cn } from '@/lib/cn'
import styles from './FolderCheckTree.module.css'

interface Props {
  /** Currently selected folder paths (relative to the library root). */
  selected: Set<string>
  /** Toggle a single folder path on/off. */
  onToggle: (path: string) => void
}

/** Nested folder picker with a checkbox per folder, mirroring the Browse
 *  sidebar tree. Selection feeds a group's comma-separated `folders` string. */
export function FolderCheckTree({ selected, onToggle }: Props) {
  const { data, isLoading, isError } = useQuery({ queryKey: ['folders'], queryFn: fetchFolders })
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  function toggleExpand(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(path) ? next.delete(path) : next.add(path)
      return next
    })
  }

  if (isLoading) return <Loading label="Loading folders…" />
  if (isError || !data) return <EmptyState icon={<FolderIcon size={32} />} title="Couldn’t load folders" />

  const rows = visibleFolders(data.tree, expanded)
  if (rows.length === 0) {
    return <EmptyState icon={<FolderIcon size={32} />} title="No subfolders">The library is a single flat folder.</EmptyState>
  }

  return (
    <div className={styles.tree}>
      {rows.map(({ node, depth }) => {
        const expandable = hasSubfolders(node)
        const isOpen = expanded.has(node.path)
        const checked = selected.has(node.path)
        return (
          <div
            key={node.path}
            className={cn(styles.row, checked && styles.rowChecked)}
            style={{ paddingLeft: `calc(${depth} * 16px + 0.4em)` }}
          >
            {expandable ? (
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
            <label className={styles.label}>
              <input type="checkbox" checked={checked} onChange={() => onToggle(node.path)} />
              <span className={styles.name} title={node.path}>
                {node.name}
              </span>
            </label>
            <span className={styles.count}>{node.file_count}</span>
          </div>
        )
      })}
    </div>
  )
}
