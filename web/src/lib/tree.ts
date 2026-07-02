import type { FileNode, FolderNode, TreeNode } from '@/api/types'

export function isFolder(n: TreeNode): n is FolderNode {
  return n.type === 'folder'
}
export function isFile(n: TreeNode): n is FileNode {
  return n.type === 'file'
}

export interface FolderRow {
  node: FolderNode
  depth: number
}

/** Flatten folders (excluding the root itself) for a sidebar, honoring which
 *  folders are currently expanded. */
export function visibleFolders(root: FolderNode, expanded: Set<string>): FolderRow[] {
  const rows: FolderRow[] = []
  const walk = (folder: FolderNode, depth: number) => {
    for (const c of folder.children) {
      if (!isFolder(c)) continue
      rows.push({ node: c, depth })
      if (expanded.has(c.path)) walk(c, depth + 1)
    }
  }
  walk(root, 0)
  return rows
}

export function hasSubfolders(node: FolderNode): boolean {
  return node.children.some(isFolder)
}
