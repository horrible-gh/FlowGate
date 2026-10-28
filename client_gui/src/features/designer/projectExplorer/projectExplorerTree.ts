// flowgate.v02.0023 — Designer Project Explorer display tree (R0001 §2.2/§2.3, D0002 §6/§9).
//
// The Project Explorer does not own tree data. It reads the canonical flat group tree the
// project store caches (`useProjectStore().fetchGroupTree`) and derives a read-only display
// shape from it, re-labelled with the v0.2 meaning:
//
//   v0.1 project  -> v0.2 Project
//   v0.1 module   -> v0.2 Group
//   v0.1 group    -> v0.2 Flow
//   v0.1 document -> v0.2 Node (Canvas responsibility; never part of this tree)
//
// Nothing here is stored: ProjectExplorer recomputes it from the node list and the search
// query, so there is no second tree store to keep in sync.

import type { GroupTreeNode } from '@/features/project'

export interface ExplorerFlow {
  /** v0.1 group id — the id a Designer uses to open the Flow. */
  id: string
  /** v0.1 module node id of the owning Group. */
  groupId: string
  name: string
  /** v0.1 group number (e.g. "0023"), when the node carries one. */
  number: string | null
}

export interface ExplorerGroup {
  /** v0.1 module node id. */
  id: string
  projectId: string
  name: string
  /** v0.1 module key (the server's `label`), the value a Flow create sends as `module`. */
  moduleName: string
  flows: ExplorerFlow[]
}

export interface ExplorerProject {
  /** v0.1 project node id (e.g. "project:flowgate"). */
  id: string
  name: string
  groups: ExplorerGroup[]
}

/** Payload ProjectExplorer emits upward for a Group selection. */
export interface ProjectExplorerGroupEvent {
  /** Project id (the store's current project id, not the tree node id). */
  projectId: string
  /** v0.1 module node id. */
  groupId: string
}

/** Payload ProjectExplorer emits upward for a Flow selection / activation intent. */
export interface ProjectExplorerFlowEvent extends ProjectExplorerGroupEvent {
  /** v0.1 group id. */
  flowId: string
  flowName: string
}

/** The one row the user last clicked. Selection is not activation (D0002 §10). */
export type ExplorerSelection = { kind: 'project' | 'group' | 'flow'; id: string } | null

/**
 * flowgate.v02.0043 — what the Designer asks the Project Explorer to show after a File menu
 * action: the selection to set (or clear) and the rows to open / close, by id. `seq` makes
 * every request distinct, so the same request made twice is applied twice.
 */
export interface ExplorerStateRequest {
  seq: number
  select: ExplorerSelection
  expand?: readonly string[]
  collapse?: readonly string[]
  /**
   * flowgate.v02.0105 T#1: false = the selection is set without opening the rows above it (the F5 restore gives the
   * open Groups as they were). Omitted = they are opened with it, as a File menu opening does.
   */
  reveal?: false
}

/** Display tree after the search filter, plus the ids a search needs revealed. */
export interface FilteredExplorerTree {
  project: ExplorerProject | null
  /** Project/Group ids whose subtree holds a match — expanded while the query is active. */
  revealIds: Set<string>
  /** Ids of rows whose own name matched (empty without a query). */
  matchIds: Set<string>
}

/** The tree node id the server gives a project's root row. */
export function projectNodeId(projectId: string): string {
  return `project:${projectId}`
}

function childrenIndex(nodes: GroupTreeNode[]): Map<string, GroupTreeNode[]> {
  const byParent = new Map<string, GroupTreeNode[]>()
  for (const node of nodes) {
    if (node.parentId === null) continue
    const list = byParent.get(node.parentId)
    if (list) list.push(node)
    else byParent.set(node.parentId, [node])
  }
  return byParent
}

/** Build the Project > Group > Flow display tree from the flat group tree.
 *
 *  - Documents (v0.2 Nodes) are dropped.
 *  - `orphan` buckets are dropped: they hold documents without a group row, not a Flow a
 *    Designer could open.
 *  - A group nested under another group is still a Flow of the nearest module, so the
 *    display stays exactly three levels deep.
 *  - Order follows the server's node order, like the Dashboard tree.
 *
 *  `fallbackProjectName` labels the root when the payload has no project node (e.g. an
 *  empty tree for a project that exists). Returns null when there is neither. */
export function buildExplorerProject(
  nodes: GroupTreeNode[],
  rootId: string | null,
  fallbackProjectName?: string | null,
): ExplorerProject | null {
  const byParent = childrenIndex(nodes)
  const projectNode =
    (rootId ? nodes.find((n) => n.id === rootId) : undefined) ??
    nodes.find((n) => n.kind === 'project' && n.parentId === null)
  if (!projectNode && !rootId) return null
  const id = projectNode?.id ?? rootId!
  const project: ExplorerProject = {
    id,
    name: projectNode?.label || fallbackProjectName || id,
    groups: [],
  }
  for (const moduleNode of byParent.get(id) ?? []) {
    if (moduleNode.kind !== 'module') continue
    // The server puts a module's display title in `title` and its key in `label`.
    const group: ExplorerGroup = {
      id: moduleNode.id,
      projectId: id,
      name: moduleNode.title || moduleNode.label,
      moduleName: moduleNode.label,
      flows: [],
    }
    // Depth-first over non-document descendants so nested groups stay in reading order.
    const stack = [...(byParent.get(moduleNode.id) ?? [])].reverse()
    const visited = new Set<string>()
    while (stack.length > 0) {
      const child = stack.pop()!
      if (visited.has(child.id)) continue
      visited.add(child.id)
      if (child.kind !== 'group' && child.kind !== 'subgroup') continue
      group.flows.push({ id: child.id, groupId: moduleNode.id, name: child.label, number: child.number })
      const grandchildren = byParent.get(child.id)
      if (grandchildren) stack.push(...[...grandchildren].reverse())
    }
    project.groups.push(group)
  }
  return project
}

export function normalizeExplorerQuery(query: string): string {
  return query.trim().toLocaleLowerCase()
}

function matches(name: string, q: string): boolean {
  return name.toLocaleLowerCase().includes(q)
}

/** Filter the display tree by Project/Group/Flow name while keeping the hierarchy:
 *
 *  - Project matches -> the whole project stays as it is.
 *  - Group matches   -> that Group with all of its Flows, under its Project.
 *  - Flow matches    -> that Flow under its Group and Project.
 *
 *  The input is never mutated; a new tree is returned. With an empty query the input
 *  project is returned unchanged and nothing is force-revealed. */
export function filterExplorerProject(
  project: ExplorerProject | null,
  query: string,
): FilteredExplorerTree {
  const q = normalizeExplorerQuery(query)
  const revealIds = new Set<string>()
  const matchIds = new Set<string>()
  if (!project || !q) return { project, revealIds, matchIds }

  const projectMatched = matches(project.name, q)
  if (projectMatched) matchIds.add(project.id)
  const groups: ExplorerGroup[] = []
  for (const group of project.groups) {
    const groupMatched = matches(group.name, q)
    const flows = group.flows.filter((flow) => {
      const hit = matches(flow.name, q)
      if (hit) matchIds.add(flow.id)
      return hit
    })
    if (groupMatched) matchIds.add(group.id)
    if (flows.length > 0) {
      // A matching Flow needs both ancestors open to be seen.
      revealIds.add(project.id)
      revealIds.add(group.id)
    } else if (groupMatched) {
      revealIds.add(project.id)
    }
    if (groupMatched || projectMatched) groups.push(group)
    else if (flows.length > 0) groups.push({ ...group, flows })
  }
  if (!projectMatched && groups.length === 0) return { project: null, revealIds, matchIds }
  return { project: { ...project, groups }, revealIds, matchIds }
}
