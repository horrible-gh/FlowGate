// flowgate.v02.0105 T#1 (NR flowgate.v02.0101.0003 §5.4, Q #510 E-1) — the Designer screen of this browser
// tab, kept so an F5 brings it back: the current Project, its Explorer Group context (the selected row and
// the open Groups), the Flow tabs in order, the active Flow and the left / right panel tabs. Same boundary rule
// as currentProjectStorage.ts / recentStorage.ts: no other module reads or writes this key.
//
// sessionStorage, not localStorage: it survives an F5 but not a new browser tab or a restart (the v02.0040
// "opened in this session" sense), and every browser tab keeps its own, so two tabs never overwrite each other.
// The key follows the per-user rule `flowgate.user.<username>.…` (conversation/drafts.ts, core/auth/logout.ts),
// so a logout clears it with the user's other keys. A stored value of another schema or one that does not
// parse is read as nothing. Best effort: a storage failure never blocks the screen.

export const DESIGNER_SESSION_VERSION = 1

export interface DesignerSessionSelection {
  kind: 'project' | 'group' | 'flow'
  id: string
}

export interface DesignerSessionExplorer {
  selection: DesignerSessionSelection | null
  /** The open Groups only (a closed Group is absent). */
  expandedGroupIds: string[]
}

export interface DesignerSessionTab {
  flowId: string
  name: string
  groupId: string | null
}

export interface DesignerSessionSnapshot {
  v: typeof DESIGNER_SESSION_VERSION
  projectId: string
  explorer: DesignerSessionExplorer
  tabs: DesignerSessionTab[]
  activeFlowId: string | null
  leftMode: string
  rightTool: string
  /**
   * Saved items an F5 restore has not confirmed yet (its tree read failed or is slow) and the user has not changed
   * meanwhile; the next restore takes them over the fields above. An absent active Flow / panel tab is the user's.
   */
  pending?: DesignerSessionPending
}

export interface DesignerSessionPending {
  tabs: DesignerSessionTab[]
  explorer: DesignerSessionExplorer
  activeFlowId?: string | null
  leftMode?: string
  rightTool?: string
}

export function designerSessionKey(username: string): string {
  return `flowgate.user.${username}.designer.session`
}

const isText = (value: unknown): value is string => typeof value === 'string' && value.length > 0
const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

function toSelection(value: unknown): DesignerSessionSelection | null | undefined {
  if (value === null) return null
  if (!isRecord(value) || !isText(value.id)) return undefined
  return value.kind === 'project' || value.kind === 'group' || value.kind === 'flow' ? { kind: value.kind, id: value.id } : undefined
}

function toExplorer(value: unknown): DesignerSessionExplorer | undefined {
  if (!isRecord(value) || !Array.isArray(value.expandedGroupIds) || !value.expandedGroupIds.every(isText)) return undefined
  const selection = toSelection(value.selection)
  return selection === undefined ? undefined : { selection, expandedGroupIds: [...value.expandedGroupIds] }
}

function toTabs(value: unknown): DesignerSessionTab[] | undefined {
  if (!Array.isArray(value)) return undefined
  const tabs: DesignerSessionTab[] = []
  for (const tab of value) {
    if (!isRecord(tab) || !isText(tab.flowId) || typeof tab.name !== 'string') return undefined
    if (tab.groupId !== null && !isText(tab.groupId)) return undefined
    tabs.push({ flowId: tab.flowId, name: tab.name, groupId: tab.groupId })
  }
  return tabs
}

/** The stored snapshot of schema v1, or null (none, another schema, unreadable). Pure apart from the read. */
export function parseDesignerSession(raw: string | null): DesignerSessionSnapshot | null {
  if (!raw) return null
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return null
  }
  if (!isRecord(value) || value.v !== DESIGNER_SESSION_VERSION || !isText(value.projectId)) return null
  const explorer = toExplorer(value.explorer)
  const tabs = toTabs(value.tabs)
  if (!explorer || !tabs || !isText(value.leftMode) || !isText(value.rightTool)) return null
  if (value.activeFlowId !== null && !isText(value.activeFlowId)) return null
  const snapshot: DesignerSessionSnapshot = {
    v: DESIGNER_SESSION_VERSION,
    projectId: value.projectId,
    explorer,
    tabs,
    activeFlowId: value.activeFlowId,
    leftMode: value.leftMode,
    rightTool: value.rightTool,
  }
  if (value.pending !== undefined) {
    const pending = toPending(value.pending)
    if (!pending) return null
    snapshot.pending = pending
  }
  return snapshot
}

function toPending(value: unknown): DesignerSessionPending | undefined {
  if (!isRecord(value)) return undefined
  const tabs = toTabs(value.tabs)
  const explorer = toExplorer(value.explorer)
  if (!tabs || !explorer) return undefined
  const pending: DesignerSessionPending = { tabs, explorer }
  if (value.activeFlowId !== undefined) {
    if (value.activeFlowId !== null && !isText(value.activeFlowId)) return undefined
    pending.activeFlowId = value.activeFlowId
  }
  for (const panel of ['leftMode', 'rightTool'] as const) {
    if (value[panel] === undefined) continue
    const mode = value[panel]
    if (!isText(mode)) return undefined
    pending[panel] = mode
  }
  return pending
}

export function readDesignerSession(username: string): DesignerSessionSnapshot | null {
  try {
    return parseDesignerSession(sessionStorage.getItem(designerSessionKey(username)))
  } catch {
    return null
  }
}

export function writeDesignerSession(username: string, snapshot: DesignerSessionSnapshot): void {
  try {
    sessionStorage.setItem(designerSessionKey(username), JSON.stringify(snapshot))
  } catch {
    /* best effort */
  }
}

export function clearDesignerSession(username: string): void {
  try {
    sessionStorage.removeItem(designerSessionKey(username))
  } catch {
    /* best effort */
  }
}
