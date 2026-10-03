import type { FileNode, GroupChangeData } from '../../../stores/explorer'

// flowgate.v02.0024 T0003 — pure tree projection for the Designer File Explorer.
// Kept free of Vue/store state so the "전체 / 변경됨 / 찾기" rules can be pinned by
// plain unit tests, and so FileTree stays a renderer instead of a decision tree.

/** The active Flow the Designer hands to the explorer. A Flow is backed by a group
 *  worktree; `groupId` is the key the existing Git/file contracts understand. */
export interface DesignerFlowScope {
  groupId: string
  name: string
  branch?: string | null
}

export type ExplorerFilter = 'all' | 'changed'

/** Payload of every intent the explorer forwards to the Designer. `canMutate` already
 *  folds in the readonly prop, the slot's live-worktree flag and the AI-busy lock. */
export interface DesignerFileIntent {
  projectId: string
  groupId: string
  node: FileNode
  commit: string | null
  canMutate: boolean
}

/** Base-relative change status as the Designer shows it. The server's untracked marker
 *  ('?') is an added file from the Base branch's point of view, so it folds into 'A';
 *  any other git letter (T, C, U, ...) still means "content differs" and reads as 'M'. */
export type ChangeStatus = 'M' | 'A' | 'D'

export type BaseUnresolvedReason =
  | 'base_unconfigured' // project has no configured Base branch (Git disabled / unset)
  | 'base_unavailable' // project Git status could not be read, so Base is unknown
  | 'base_unknown' // the change set did not say which Base it compared against
  | 'base_mismatch' // the change set compared against a Base other than the project Base

export type ChangeState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; baseBranch: string; statuses: Record<string, ChangeStatus> }
  | { kind: 'unresolved'; reason: BaseUnresolvedReason; expected: string | null; actual: string | null }
  | { kind: 'error' }

export interface ExplorerRow {
  node: FileNode
  depth: number
  hasChildren: boolean
  expanded: boolean
  status: ChangeStatus | null
  /** Folder only: number of changed files underneath (0 when changes are not ready). */
  changedCount: number
}

export function normalizePath(path: string): string {
  return path.replace(/\\/g, '/').replace(/^\/+/, '').replace(/\/+$/, '')
}

export function toChangeStatus(raw: string): ChangeStatus {
  const letter = (raw || '').trim().charAt(0).toUpperCase()
  if (letter === 'D') return 'D'
  if (letter === 'A' || letter === '?') return 'A'
  return 'M'
}

export function toStatusMap(changes: GroupChangeData[]): Record<string, ChangeStatus> {
  const out: Record<string, ChangeStatus> = {}
  for (const change of changes) {
    const path = normalizePath(change.path || '')
    if (path) out[path] = toChangeStatus(change.status)
  }
  return out
}

function ancestorsOf(path: string): string[] {
  const segs = path.split('/')
  const out: string[] = []
  for (let i = 1; i < segs.length; i += 1) out.push(segs.slice(0, i).join('/'))
  return out
}

/** Nodes for the "변경됨" view: every changed path plus its ancestor folders. A path the
 *  Flow deleted AND committed is no longer in the HEAD tree, so it is synthesized as a
 *  read-only virtual node — otherwise the deletion would vanish from the changed list. */
export function changedViewNodes(nodes: FileNode[], statuses: Record<string, ChangeStatus>): FileNode[] {
  const byPath = new Map<string, FileNode>()
  for (const n of nodes) byPath.set(normalizePath(n.path), n)
  const keep = new Set<string>()
  const synthesized: FileNode[] = []

  const ensure = (path: string, type: 'folder' | 'file') => {
    if (keep.has(path)) return
    keep.add(path)
    if (byPath.has(path)) return
    const segs = path.split('/')
    const name = segs[segs.length - 1]
    const parentPath = segs.slice(0, -1).join('/')
    const virtual: FileNode = {
      id: `chg:${path}`,
      parent_id: parentPath ? (byPath.get(parentPath)?.id ?? `chg:${parentPath}`) : null,
      type,
      name,
      label: name,
      path,
      permissions: ['read'],
    }
    byPath.set(path, virtual)
    synthesized.push(virtual)
  }

  for (const path of Object.keys(statuses).sort()) {
    for (const ancestor of ancestorsOf(path)) ensure(ancestor, 'folder')
    ensure(path, byPath.get(path)?.type === 'folder' ? 'folder' : 'file')
  }
  return [...nodes.filter((n) => keep.has(normalizePath(n.path))), ...synthesized]
}

/** Nodes matching a name query plus their ancestors. Returns the matched set too so the
 *  tree can auto-expand the path down to each hit while a search is active. */
export function searchNodes(nodes: FileNode[], query: string): { nodes: FileNode[]; reveal: Set<string> } {
  const q = query.trim().toLowerCase()
  if (!q) return { nodes, reveal: new Set() }
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const keep = new Set<string>()
  const reveal = new Set<string>()
  for (const n of nodes) {
    if (!n.label.toLowerCase().includes(q) && !n.name.toLowerCase().includes(q)) continue
    keep.add(n.id)
    let parentId = n.parent_id
    while (parentId) {
      keep.add(parentId)
      reveal.add(parentId)
      parentId = byId.get(parentId)?.parent_id ?? null
    }
  }
  return { nodes: nodes.filter((n) => keep.has(n.id)), reveal }
}

function sortChildren(a: FileNode, b: FileNode): number {
  if (a.type !== b.type) return a.type === 'folder' ? -1 : 1
  return a.name.localeCompare(b.name)
}

/** Flatten the visible tree into rows (depth-first, folders first). A folder's children
 *  appear only while it is expanded — `isExpanded` is the caller's single source. */
export function flattenRows(
  nodes: FileNode[],
  isExpanded: (node: FileNode) => boolean,
  statuses: Record<string, ChangeStatus> | null,
): ExplorerRow[] {
  const children = new Map<string | null, FileNode[]>()
  const ids = new Set(nodes.map((n) => n.id))
  for (const n of nodes) {
    // A node whose parent is not in this (filtered) set is treated as a root.
    const parent = n.parent_id && ids.has(n.parent_id) ? n.parent_id : null
    const list = children.get(parent) ?? []
    list.push(n)
    children.set(parent, list)
  }
  for (const list of children.values()) list.sort(sortChildren)

  const changedPaths = statuses ? Object.keys(statuses) : []
  const changedUnder = (folderPath: string) => {
    const prefix = normalizePath(folderPath) + '/'
    return changedPaths.filter((p) => p.startsWith(prefix)).length
  }

  const rows: ExplorerRow[] = []
  const walk = (parent: string | null, depth: number) => {
    for (const node of children.get(parent) ?? []) {
      const kids = children.get(node.id) ?? []
      const isFolder = node.type === 'folder'
      const expanded = isFolder && isExpanded(node)
      rows.push({
        node,
        depth,
        hasChildren: isFolder && kids.length > 0,
        expanded,
        status: !isFolder && statuses ? (statuses[normalizePath(node.path)] ?? null) : null,
        changedCount: isFolder ? changedUnder(node.path) : 0,
      })
      if (expanded) walk(node.id, depth + 1)
    }
  }
  walk(null, 0)
  return rows
}
