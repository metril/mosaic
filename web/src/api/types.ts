/* API response types. Hand-written from the FastAPI/Pydantic models — the
   surface is stable and this avoids a codegen step. Expanded per phase. */

export interface AuthUser {
  sub: string
  email: string
  name: string
}

export interface FileNode {
  type: 'file'
  name: string
  path: string
  size: number
  size_formatted: string
  dimensions: string
  width: number | null
  height: number | null
  format?: string | null
  mode?: string | null
  modified: string | null
  error?: string
}

export interface FolderNode {
  type: 'folder'
  name: string
  path: string
  children: TreeNode[]
  /** Recursive count — propagated up from children by the server. */
  file_count: number
  folder_count: number
  stats?: unknown
  error?: string
}

export type TreeNode = FileNode | FolderNode

/** Shape of GET /browse?format=json — the root folder is under `tree`. */
export interface BrowseTree {
  root: string
  total_folders: number
  total_files: number
  tree: FolderNode
}

export interface CurationState {
  favorites: string[]
  excluded: string[]
}
