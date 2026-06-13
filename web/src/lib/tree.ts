import type { FileNode, FolderNode, TreeNode } from '@/api/types'

export function isFolder(n: TreeNode): n is FolderNode {
  return n.type === 'folder'
}
export function isFile(n: TreeNode): n is FileNode {
  return n.type === 'file'
}

/** All image files anywhere under `node`, depth-first. */
export function collectFiles(node: FolderNode): FileNode[] {
  const out: FileNode[] = []
  const walk = (f: FolderNode) => {
    for (const c of f.children) {
      if (isFile(c)) out.push(c)
      else walk(c)
    }
  }
  walk(node)
  return out
}

/** Direct image-file children of `node`. */
export function directFiles(node: FolderNode): FileNode[] {
  return node.children.filter(isFile)
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

export function findFolder(root: FolderNode, path: string): FolderNode | null {
  if (root.path === path) return root
  let found: FolderNode | null = null
  const walk = (f: FolderNode) => {
    for (const c of f.children) {
      if (!isFolder(c)) continue
      if (c.path === path) {
        found = c
        return
      }
      walk(c)
    }
  }
  walk(root)
  return found
}

export function hasSubfolders(node: FolderNode): boolean {
  return node.children.some(isFolder)
}
