import type { FolderNode } from '@/api/types'
import { visibleFolders, hasSubfolders } from '@/lib/tree'
import { cn } from '@/lib/cn'
import { ChevronRightIcon, ImageIcon } from '@/components/ui'
import styles from './Browse.module.css'

interface Props {
  root: FolderNode
  selected: string
  expanded: Set<string>
  onSelect: (path: string) => void
  onToggle: (path: string) => void
}

export function FolderTree({ root, selected, expanded, onSelect, onToggle }: Props) {
  const rows = visibleFolders(root, expanded)
  const rootCount = root.file_count

  return (
    <div className={styles.sidebar}>
      <div className={cn('eyebrow', styles.sideLabel)}>Folders</div>

      <button
        className={cn(styles.treeRow, selected === root.path && styles.treeActive)}
        onClick={() => onSelect(root.path)}
      >
        <span className={styles.twistySpacer} />
        <ImageIcon size={15} />
        <span className={styles.treeName}>All photos</span>
        <span className={styles.treeCount}>{rootCount}</span>
      </button>

      {rows.map(({ node, depth }) => {
        const expandable = hasSubfolders(node)
        const isOpen = expanded.has(node.path)
        return (
          <button
            key={node.path}
            className={cn(styles.treeRow, selected === node.path && styles.treeActive)}
            style={{ paddingLeft: `calc(${depth} * 14px + 0.5em)` }}
            onClick={() => onSelect(node.path)}
          >
            <span
              className={cn(styles.twisty, isOpen && styles.twistyOpen)}
              onClick={(e) => {
                if (!expandable) return
                e.stopPropagation()
                onToggle(node.path)
              }}
            >
              {expandable && <ChevronRightIcon size={13} />}
            </span>
            <span className={styles.treeName}>{node.name}</span>
            <span className={styles.treeCount}>{node.file_count}</span>
          </button>
        )
      })}
    </div>
  )
}
