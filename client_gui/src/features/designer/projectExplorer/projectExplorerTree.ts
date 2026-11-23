// Designer Project Explorer display tree.
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
import { flowLabelOfNumber } from '../center/graph/cardIds'

export interface ExplorerFlow {
  archived?: boolean
  /** v0.1 group id — the id a Designer uses to open the Flow. */
  id: string
  /** v0.1 module node id of the owning Group. */
  groupId: string
  name: string
  /** v0.1 group number (e.g. "0023"), when the node carries one. */
  number: string | null
  /**
   * flowgate.v02test.0077: the Flow's display number (`"F77"`) drawn ahead of its name — the server's `flow_label`,
   * else made from `number` the same way (F + the number without leading zeros). Absent when neither is known.
   */
  label?: string | null
}

export interface ExplorerGroup {
  /** v0.1 module node id. */
  id: string
  projectId: string
  name: string
  /** v0.1 module key (the server's `label`), the value a Flow create sends as `module`. */
  moduleName: string
  flows: ExplorerFlow[]
  /** v02gui.0027: the Group is display-archived (only present in an "아카이브 포함" tree). */
  archived?: boolean
  /**
   * v02gui.0027 (0008-T): every Flow of the Group on the server (archived and discarded included), unlike `flows`,
   * which follows the tree's filters. What the Group menu says ahead of a delete; absent when the server did not say.
   */
  flowTotal?: number
  /**
   * flowgate.v02test.0077: the widest Flow number of the Group, in characters (`F77` = 3, `F100` = 4). Every Flow row
   * of the Group gives its number that width, so the names start at the same place on every line.
   */
  flowLabelWidth?: number
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

/** The one row the user last clicked. Selection is not activation. */
export type ExplorerSelection = { kind: 'project' | 'group' | 'flow'; id: string } | null

/**
 * what the Designer asks the Project Explorer to show after a File menu
 * action: the selection to set (or clear) and the rows to open / close, by id. `seq` makes
 * every request distinct, so the same request made twice is applied twice.
 */
export interface ExplorerStateRequest {
  seq: number
  select: ExplorerSelection
  expand?: readonly string[]
  collapse?: readonly string[]
  /**
   * false = the selection is set without opening the rows above it (the F5 restore gives the
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
  /** flowgate.v02test.0077: ids of the Flows the query named by number (`F77`, `77`, `F77-12`, …), in tree order. */
  idMatchIds: string[]
}

/** The tree node id the server gives a project's root row. */
export function projectNodeId(projectId: string): string {
  return `project:${projectId}`
}

/** `"0077"` -> `"F77"`; null for a missing or non-numeric number. The shared client rule (center/graph/cardIds). */
export const flowLabelOf = flowLabelOfNumber

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
      ...(moduleNode.archivedAt ? { archived: true } : {}),
      ...(typeof moduleNode.flowTotal === 'number' ? { flowTotal: moduleNode.flowTotal } : {}),
    }
    // Depth-first over non-document descendants so nested groups stay in reading order.
    const stack = [...(byParent.get(moduleNode.id) ?? [])].reverse()
    const visited = new Set<string>()
    while (stack.length > 0) {
      const child = stack.pop()!
      if (visited.has(child.id)) continue
      visited.add(child.id)
      if (child.kind !== 'group' && child.kind !== 'subgroup') continue
      const label = child.flowLabel || flowLabelOf(child.number)
      group.flows.push({ id: child.id, groupId: moduleNode.id, name: child.label, number: child.number,
        ...(label ? { label } : {}),
        ...(child.archivedAt ? { archived: true } : {}) })
      const grandchildren = byParent.get(child.id)
      if (grandchildren) stack.push(...[...grandchildren].reverse())
    }
    const width = Math.max(0, ...group.flows.map((flow) => flow.label?.length ?? 0))
    if (width > 0) group.flowLabelWidth = width
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

/**
 * flowgate.v02test.0077 (0005-T §4): a Flow / card display id typed into the search.
 *
 * - `flow`: `F77`, `f0077`, `v02test.F77`, `flowgate.v02test.F77`, or digits only (`77`, `bare`).
 * - `card`: the same with a card number — `F77-12`, `F77-012`, `v02test.F77-12`, `flowgate.v02test.F77-12`.
 * - `local`: `#12`, a card of the Flow the Designer has open.
 *
 * Case and leading zeros are ignored. A stored id (`flowgate.v02test.0077`, `…0077.0003-T`) is not a display id.
 */
export type ExplorerIdQuery =
  | { kind: 'flow'; flowNo: number; project?: string; module?: string; bare?: true }
  | { kind: 'card'; flowNo: number; cardNo: number; project?: string; module?: string }
  | { kind: 'local'; cardNo: number }

const DISPLAY_ID = /^(?:([a-z0-9_-]+)\.)?(?:([a-z0-9_-]+)\.)?f(\d+)(?:-(\d+))?$/

export function parseExplorerId(query: string): ExplorerIdQuery | null {
  const q = normalizeExplorerQuery(query)
  if (!q) return null
  const local = /^#(\d+)$/.exec(q)
  if (local) return { kind: 'local', cardNo: Number.parseInt(local[1], 10) }
  if (/^\d+$/.test(q)) return { kind: 'flow', flowNo: Number.parseInt(q, 10), bare: true }
  const id = DISPLAY_ID.exec(q)
  if (!id) return null
  const scope = [id[1], id[2]].filter((part): part is string => !!part)
  const where = {
    ...(scope.length === 2 ? { project: scope[0] } : {}),
    ...(scope.length > 0 ? { module: scope[scope.length - 1] } : {}),
  }
  const flowNo = Number.parseInt(id[3], 10)
  if (id[4] === undefined) return { kind: 'flow', flowNo, ...where }
  return { kind: 'card', flowNo, cardNo: Number.parseInt(id[4], 10), ...where }
}

function flowNumberOf(flow: ExplorerFlow): number | null {
  if (flow.number && /^\d+$/.test(flow.number)) return Number.parseInt(flow.number, 10)
  const label = flow.label ? /^F(\d+)$/i.exec(flow.label) : null
  return label ? Number.parseInt(label[1], 10) : null
}

/** The Flow is the one the id names: same number, and the project / module key when the id has them. */
function idNames(id: ExplorerIdQuery | null, flow: ExplorerFlow, group: ExplorerGroup, projectKey: string, contextFlowId: string | null): boolean {
  if (!id) return false
  if (id.kind === 'local') return contextFlowId !== null && flow.id === contextFlowId
  if (id.project !== undefined && id.project !== projectKey) return false
  if (id.module !== undefined && id.module !== group.moduleName.toLocaleLowerCase()) return false
  return flowNumberOf(flow) === id.flowNo
}

/** `project:flowgate` -> `flowgate` (lower case), the project part of a full display id. */
function projectKeyOf(project: ExplorerProject): string {
  return project.id.replace(/^project:/, '').toLocaleLowerCase()
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
  options: { contextFlowId?: string | null } = {},
): FilteredExplorerTree {
  const q = normalizeExplorerQuery(query)
  const revealIds = new Set<string>()
  const matchIds = new Set<string>()
  const idMatchIds: string[] = []
  if (!project || !q) return { project, revealIds, matchIds, idMatchIds }

  // flowgate.v02test.0077: a Flow also matches by its number (`F77`, `77`, `f0077`, `F77-12`, the full id).
  const id = parseExplorerId(q)
  const projectKey = projectKeyOf(project)
  const contextFlowId = options.contextFlowId ?? null
  const projectMatched = matches(project.name, q)
  if (projectMatched) matchIds.add(project.id)
  const groups: ExplorerGroup[] = []
  for (const group of project.groups) {
    const groupMatched = matches(group.name, q)
    const flows = group.flows.filter((flow) => {
      const named = idNames(id, flow, group, projectKey, contextFlowId)
      if (named) idMatchIds.push(flow.id)
      const hit = named || matches(flow.name, q)
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
  if (!projectMatched && groups.length === 0) return { project: null, revealIds, matchIds, idMatchIds }
  return { project: { ...project, groups }, revealIds, matchIds, idMatchIds }
}
