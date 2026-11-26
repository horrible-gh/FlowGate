import { computed, reactive, ref, shallowRef, toValue, type MaybeRefOrGetter } from 'vue'
import { getRequest } from '../api'
import { onForbidden } from './forbidden'
import { onRequestLeaving } from './requestSignal'

// The one place the screens read "may I?" from. The server decides (rbac/capabilities.py) and sends, per
// target, `{ role, sources, actions: { <permission id>: { allowed, reason?, minimum_role?, rule? } } }`;
// this module only stores that answer and hands out one action's value at a time. It never works a
// permission out from a role name or from `is_admin` — a role name here is only text for a sentence.
//
// An answer that has not arrived (or failed) is "not allowed": a button stays off until the server said yes.
// The server still checks every request itself; this only decides what the screen offers.

export type CapabilityReason =
  | 'not_member' | 'role_limit' | 'context_rule' | 'token_scope'
  | 'system_admin_only' | 'workspace_disabled' | 'project_archived' | 'account_inactive'

export interface CapabilityDecision {
  readonly allowed: boolean
  /** Why it is refused (a CapabilityReason or a value this build does not know). Absent when allowed. */
  readonly reason: string | null
  /** For `role_limit`: the lowest role that has this action (`role_developer`, …). */
  readonly minimumRole: string | null
  /** For `context_rule`: the rule's name (`document_editable`, `own_draft`). */
  readonly rule: string | null
}

export interface CapabilitySource {
  readonly kind: string
  readonly role: string | null
  readonly teamName: string | null
}

export interface CapabilityBlock {
  /** Final role id of the scope (`role_viewer`, `system_admin`, …) or null when there is none. */
  readonly role: string | null
  readonly sources: readonly CapabilitySource[]
  readonly actions: Readonly<Record<string, CapabilityDecision>>
}

const text = (value: unknown): string | null => (typeof value === 'string' && value !== '' ? value : null)
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** Read one `capabilities` object of any server answer. Anything that is not that shape gives null. */
export function parseCapabilities(raw: unknown): CapabilityBlock | null {
  if (!isRecord(raw) || !isRecord(raw.actions)) return null
  const actions: Record<string, CapabilityDecision> = {}
  for (const [id, value] of Object.entries(raw.actions)) {
    if (!isRecord(value)) continue
    const allowed = value.allowed === true
    actions[id] = {
      allowed,
      reason: allowed ? null : text(value.reason) ?? 'role_limit',
      minimumRole: text(value.minimum_role),
      rule: text(value.rule),
    }
  }
  const sources = Array.isArray(raw.sources)
    ? raw.sources.filter(isRecord).map((s) => ({ kind: text(s.kind) ?? 'none', role: text(s.role), teamName: text(s.name) }))
    : []
  return { role: text(raw.role), sources, actions }
}

export const NO_DECISION: CapabilityDecision = Object.freeze({ allowed: false, reason: 'unknown', minimumRole: null, rule: null })

// ── the store ────────────────────────────────────────────────────────────────

type ProjectEntry = { status: 'loading' | 'ready' | 'failed'; block: CapabilityBlock | null; seq: number }

const systemBlock = shallowRef<CapabilityBlock | null>(null)
const projects = reactive(new Map<string, ProjectEntry>())
const inflight = new Map<string, Promise<void>>()
const invalidated = ref(0)
/** Whether the capability blocks that came with GET /api/v1/projects (one per listed project) are in. */
const projectListState = ref<'idle' | 'loading' | 'ready' | 'failed'>('idle')

// Who may publish an answer. Every read of the server's capabilities — /auth/me, a project's capabilities, the
// project list rows, the workspace list — takes a `CapabilityRead` BEFORE it asks and hands it back with the answer:
//  - `epoch` is the session. Sign-out and an account switch (`resetCapabilities`) start a new one, so an answer that
//    left before that can never fill the next person's store (the earlier person's user state, role and buttons).
//  - `seq` is the order the reads were started in. An answer is dropped when the store already holds one of a read
//    started later (two answers that arrive in the wrong order leave the newer one's).
//  - `invalidateCapabilities` (a 403: what the screen believes may be old) raises `floor`: a project or workspace
//    answer of a read started before it is dropped, because the invalidation asks again.
let epoch = 0
let readSeq = 0
let floor = 0
let systemSeq = 0
let workspaceSeq = 0

export interface CapabilityRead {
  readonly epoch: number
  readonly seq: number
}

/** Take the token of a read about to start. */
export function startCapabilityRead(): CapabilityRead {
  return { epoch, seq: ++readSeq }
}

/** The read still belongs to the session that started it. */
export function isCapabilityReadCurrent(read: CapabilityRead): boolean {
  return read.epoch === epoch
}


/**
 * Put in the system block (/auth/me). Returns false, and changes nothing, when `read` is of an earlier session or an
 * older read than the block now held. Without `read` the block is taken as the newest answer.
 */
export function setSystemCapabilities(block: CapabilityBlock | null, read?: CapabilityRead): boolean {
  if (read) {
    if (!isCapabilityReadCurrent(read) || read.seq < systemSeq) return false
    systemSeq = read.seq
  } else {
    systemSeq = ++readSeq
  }
  systemBlock.value = block
  return true
}

/**
 * Put in a project's block that came with another answer (project list row, document, canvas). Returns false when
 * the answer is of an earlier session, of a read started before the last invalidation, or older than the answer the
 * store already holds for that project.
 */
export function setProjectCapabilities(projectId: string, block: CapabilityBlock | null, read?: CapabilityRead): boolean {
  if (!projectId || !block) return false
  let seq: number
  if (read) {
    if (!isCapabilityReadCurrent(read) || read.seq <= floor) return false
    const held = projects.get(projectId)
    if (held && held.status === 'ready' && held.seq > read.seq) return false
    seq = read.seq
  } else {
    seq = ++readSeq
  }
  projects.set(projectId, { status: 'ready', block, seq })
  return true
}

/** The rows of GET /api/v1/projects (each with its `capabilities`): the same rule for every row. */
export function setProjectCapabilityRows(rows: readonly { project_id?: unknown; capabilities?: unknown }[], read?: CapabilityRead): void {
  for (const row of rows) {
    const id = text(row.project_id)
    if (id) setProjectCapabilities(id, parseCapabilities(row.capabilities), read)
  }
}

/** GET /api/v1/projects/{id}/capabilities once at a time per project. */
export function loadProjectCapabilities(projectId: string, options: { force?: boolean } = {}): Promise<void> {
  if (!projectId) return Promise.resolve()
  const current = projects.get(projectId)
  if (!options.force && current?.status === 'ready') return Promise.resolve()
  const pending = inflight.get(projectId)
  if (pending) return pending
  const read = startCapabilityRead()
  // The block on show stays until the answer is in: a re-ask never turns the buttons off and on again.
  if (!current?.block) projects.set(projectId, { status: 'loading', block: null, seq: read.seq })
  const usable = () => isCapabilityReadCurrent(read) && read.seq > floor
  const request = Promise.resolve(getRequest<{ capabilities?: unknown }>(`/api/v1/projects/${encodeURIComponent(projectId)}/capabilities`))
    .then((response) => {
      if (!usable()) return
      const block = parseCapabilities(response.data?.capabilities)
      if (block) {
        setProjectCapabilities(projectId, block, read)
      } else if (!projects.get(projectId)?.block) {
        projects.set(projectId, { status: 'failed', block: null, seq: read.seq })
      }
    })
    .catch(() => {
      // A failed re-ask keeps the answer on show; only a project with no answer at all is marked failed.
      if (usable() && !projects.get(projectId)?.block) projects.set(projectId, { status: 'failed', block: null, seq: read.seq })
    })
    .finally(() => { if (inflight.get(projectId) === request) inflight.delete(projectId) })
  inflight.set(projectId, request)
  return request
}

/**
 * Read the blocks of every project the person can see, from GET /api/v1/projects (the server lists only
 * those, each row with its `capabilities`). Asked once; an invalidation makes it ask again.
 */
export function ensureProjectCapabilityList(): Promise<void> {
  if (projectListState.value === 'ready' || projectListState.value === 'loading') return listPending ?? Promise.resolve()
  projectListState.value = 'loading'
  const read = startCapabilityRead()
  const usable = () => isCapabilityReadCurrent(read) && read.seq > floor
  const request: Promise<void> = Promise.resolve(getRequest<{ projects?: unknown }>('/api/v1/projects', { status: 'active' }))
    .then((response) => {
      if (!usable()) return
      const rows = Array.isArray(response.data?.projects) ? response.data.projects : []
      setProjectCapabilityRows(rows.filter(isRecord), read)
      projectListState.value = 'ready'
    })
    .catch(() => { if (usable()) projectListState.value = 'failed' })
    .finally(() => { if (listPending === request) listPending = null })
  listPending = request
  return request
}
let listPending: Promise<void> | null = null

export function projectCapabilityListState(): 'idle' | 'loading' | 'ready' | 'failed' {
  return projectListState.value
}

// ── workspaces (GET /api/v1/workspaces: every row has its own block) ─────────

interface WorkspaceEntry { id: string; isDefault: boolean; block: CapabilityBlock | null }
const workspaces = shallowRef<readonly WorkspaceEntry[]>([])
const workspaceListState = ref<'idle' | 'loading' | 'ready' | 'failed'>('idle')
let workspacePending: Promise<void> | null = null

/** Take the rows of GET /api/v1/workspaces (each with its `capabilities`) as the answer (same rule as the others). */
export function setWorkspaceCapabilityList(rows: unknown, read?: CapabilityRead): boolean {
  if (read) {
    if (!isCapabilityReadCurrent(read) || read.seq <= floor || read.seq < workspaceSeq) return false
    workspaceSeq = read.seq
  } else {
    workspaceSeq = ++readSeq
  }
  const list = Array.isArray(rows) ? rows : []
  workspaces.value = list.filter(isRecord).flatMap((row) => {
    const id = text(row.workspace_id)
    return id ? [{ id, isDefault: row.is_default === true, block: parseCapabilities(row.capabilities) }] : []
  })
  workspaceListState.value = 'ready'
  return true
}

function loadWorkspaceCapabilityList(): Promise<void> {
  if (workspacePending) return workspacePending
  if (workspaceListState.value !== 'ready') workspaceListState.value = 'loading'
  const read = startCapabilityRead()
  const usable = () => isCapabilityReadCurrent(read) && read.seq > floor
  const request: Promise<void> = Promise.resolve(getRequest<{ workspaces?: unknown }>('/api/v1/workspaces'))
    .then((response) => { if (usable()) setWorkspaceCapabilityList(response.data?.workspaces, read) })
    .catch(() => { if (usable() && workspaceListState.value === 'loading') workspaceListState.value = 'failed' })
    .finally(() => { if (workspacePending === request) workspacePending = null })
  workspacePending = request
  return request
}

export function ensureWorkspaceCapabilityList(): Promise<void> {
  if (workspaceListState.value === 'ready' || workspaceListState.value === 'loading') return workspacePending ?? Promise.resolve()
  return loadWorkspaceCapabilityList()
}

/**
 * A new project goes to the default workspace (POST /api/v1/projects without `workspace_id`), so that is the
 * workspace whose `workspace.project.create` decides the [New project] button. Asks the server once; until
 * the answer is in the button stays off.
 */
export function newProjectDecision(): CapabilityDecision {
  void invalidated.value
  if (workspaceListState.value === 'idle') void ensureWorkspaceCapabilityList()
  const target = workspaces.value.find((w) => w.isDefault)
  return decisionOf(target?.block, 'workspace.project.create')
}

/** Some project the person can see allows this action (menus that open a project's own screens). */
export function anyProjectCan(action: string): boolean {
  void invalidated.value
  for (const entry of projects.values()) {
    if (entry.block?.actions[action]?.allowed === true) return true
  }
  return false
}

/**
 * The permissions of this person may have changed (a 403, coming back to the window, another project,
 * a role change made here): drop every project's answer so the next read asks the server again. The
 * system block is refreshed by `onCapabilitiesInvalidated` listeners (currentUser re-reads /auth/me).
 */
export function invalidateCapabilities(): void {
  floor = readSeq
  inflight.clear()
  listPending = null
  workspacePending = null
  projects.clear()
  projectListState.value = 'idle'
  workspaceListState.value = 'idle'
  invalidated.value++
  for (const listener of listeners) listener()
}

const listeners = new Set<() => void>()
/** Called after every invalidation; returns the remover. */
export function onCapabilitiesInvalidated(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

// ── asking again without waiting for a refusal ───────────────────────────────
//
// The server sends no permission version, so a role change made by someone else cannot be seen by comparing
// versions, and there is no age that is safe to wait out: whatever the person does first after the change must
// already ask again. So EVERY request the person's screens send (the permission reads themselves excepted) starts a
// round that asks for the answers on show again — the system block (/auth/me, through `onCapabilitiesRefresh`), the
// project answers and the workspace answer — and so does every coming back to the window. There is no waiting
// window: a change made just before the request is asked for by that very request. The answers on show stay until the
// new ones are in, so nothing flashes; a grant turns the buttons on and a loss turns them off the moment the answer
// lands. A button that is off for a refused action does not need to be pressed for this.
//
// The load is kept down by three rules. (1) One round at a time: the requests that leave while a round is out share
// it. (2) A request that leaves while a round is out, though, may have been sent after a change that round's reads
// had already passed, so it queues exactly one more round, which starts when the running one ends (never more than
// one running and one waiting, however many requests there are). (3) A round asks for the projects with ONE project
// list read (its rows carry every project's block) and reads singly only a project the list did not carry.

const refreshListeners = new Set<() => void>()
/** Called by every round (the system block is read again by currentUser); returns the remover. */
export function onCapabilitiesRefresh(listener: () => void): () => void {
  refreshListeners.add(listener)
  return () => { refreshListeners.delete(listener) }
}

let refreshPending: Promise<void> | null = null
let refreshQueued = false

/** The projects on show: one list read for all of them, then one read for each the list did not carry. */
function refreshProjectAnswers(): Promise<void> {
  const ids = [...projects.keys()]
  if (ids.length === 0) return Promise.resolve()
  const single = (list: readonly string[]) => Promise.all(list.map((id) => loadProjectCapabilities(id, { force: true }))).then(() => undefined)
  if (ids.length === 1) return single(ids)
  const read = startCapabilityRead()
  return Promise.resolve(getRequest<{ projects?: unknown }>('/api/v1/projects', { status: 'active' }))
    .then((response): string[] => {
      if (!isCapabilityReadCurrent(read) || read.seq <= floor) return []
      const rows = (Array.isArray(response.data?.projects) ? response.data.projects : []).filter(isRecord)
      setProjectCapabilityRows(rows, read)
      const carried = new Set<string>()
      for (const row of rows) {
        const id = text(row.project_id)
        if (id && parseCapabilities(row.capabilities)) carried.add(id)
      }
      return ids.filter((id) => !carried.has(id))
    })
    .catch((): string[] => ids)
    .then(single)
}

/** Ask again for every answer on show, keeping them on screen until the new ones arrive. */
export function refreshCapabilities(): Promise<void> {
  if (refreshPending) return refreshPending
  if (systemBlock.value === null && projects.size === 0 && workspaces.value.length === 0) return Promise.resolve()
  const reads: Promise<void>[] = [refreshProjectAnswers()]
  if (workspaceListState.value === 'ready') reads.push(loadWorkspaceCapabilityList())
  for (const listener of refreshListeners) listener()
  const request: Promise<void> = Promise.all(reads)
    .then(() => undefined)
    .finally(() => {
      if (refreshPending === request) refreshPending = null
      if (refreshQueued) {
        refreshQueued = false
        void refreshCapabilities()
      }
    })
  refreshPending = request
  return request
}

/** A request is leaving: start a round, or (one is out) queue the next one. */
function requestLeft(): void {
  if (refreshPending) {
    refreshQueued = true
    return
  }
  void refreshCapabilities()
}

// The permission reads themselves must not start a round (they are the round).
const OWN_READS: readonly RegExp[] = [/^\/?auth\/me$/, /\/capabilities$/, /\/my-permissions$/, /^\/?api\/v1\/workspaces$/, /^\/?api\/v1\/projects$/]

function isOwnRead(path: string): boolean {
  const clean = (path.split('?')[0] ?? path).replace(/^https?:\/\/[^/]+/, '').replace(/^\/flowgate/, '')
  return OWN_READS.some((pattern) => pattern.test(clean))
}

onRequestLeaving((path) => {
  if (!isOwnRead(path)) requestLeft()
})

if (typeof document !== 'undefined') {
  // Back in the window: the person may have been away while the permissions changed, so ask again with no request needed.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') requestLeft()
  })
}

// A 403 from any request: what this screen believes about the person's permissions may be out of date.
// Several refusals in a burst (a screen asking for ten things) must not make ten rounds of re-asking.
const FORBIDDEN_REFRESH_GAP_MS = 3000
let lastForbiddenRefresh = 0
onForbidden(() => {
  const now = Date.now()
  if (now - lastForbiddenRefresh < FORBIDDEN_REFRESH_GAP_MS) return
  lastForbiddenRefresh = now
  invalidateCapabilities()
})

/** Forget everything (sign-out, another account); reads still on their way belong to the old session and are dropped. */
export function resetCapabilities(): void {
  epoch++
  floor = readSeq
  systemSeq = 0
  workspaceSeq = 0
  inflight.clear()
  listPending = null
  workspacePending = null
  refreshPending = null
  refreshQueued = false
  projects.clear()
  projectListState.value = 'idle'
  workspaceListState.value = 'idle'
  workspaces.value = []
  systemBlock.value = null
  invalidated.value++
}

// ── reading ──────────────────────────────────────────────────────────────────

export function decisionOf(block: CapabilityBlock | null | undefined, action: string): CapabilityDecision {
  return block?.actions[action] ?? NO_DECISION
}

/**
 * Reactive reads for one screen. `projectId` is the project whose actions `can(action)` answers; without
 * one only `system(action)` and `workspace`-less reads make sense. A project's answer is asked for as
 * soon as a project id is known and re-asked after an invalidation.
 */
export function useCapabilities(projectId?: MaybeRefOrGetter<string | null | undefined>) {
  const id = computed(() => (projectId ? toValue(projectId) ?? null : null))
  const entry = computed<ProjectEntry | null>(() => {
    void invalidated.value
    const key = id.value
    if (!key) return null
    const found = projects.get(key)
    if (!found) void loadProjectCapabilities(key)
    return found ?? null
  })
  const projectBlock = computed(() => entry.value?.block ?? null)
  return {
    /** The project's answer is here. */
    ready: computed(() => entry.value?.status === 'ready'),
    role: computed(() => projectBlock.value?.role ?? null),
    sources: computed(() => projectBlock.value?.sources ?? []),
    decision: (action: string): CapabilityDecision => decisionOf(projectBlock.value, action),
    can: (action: string): boolean => decisionOf(projectBlock.value, action).allowed,
    /** The server's "no" for this action, or null (allowed, or not answered yet). */
    refusal: (action: string): CapabilityDecision | null => {
      const decision = projectBlock.value?.actions[action]
      return decision && !decision.allowed ? decision : null
    },
    systemDecision: (action: string): CapabilityDecision => decisionOf(systemBlock.value, action),
    systemCan: (action: string): boolean => decisionOf(systemBlock.value, action).allowed,
    systemReady: computed(() => systemBlock.value !== null),
  }
}

/**
 * The server's refusal of one action, or null when it allowed it OR no answer is in yet. Buttons inside a
 * project use this: they go off only on a "no" the server gave (a missing answer must not turn a screen
 * off — the server still checks every request, and its 403 shows the common box).
 */
export function projectRefusal(projectId: string | null | undefined, action: string): CapabilityDecision | null {
  if (!projectId) return null
  void invalidated.value
  const found = projects.get(projectId)
  if (!found) {
    void loadProjectCapabilities(projectId)
    return null
  }
  const decision = found.block?.actions[action]
  return decision && !decision.allowed ? decision : null
}

/** The person's final role in a project as the server last said it (reactive; null: no answer yet). Text for a sentence only. */
export function projectRole(projectId: string | null | undefined): string | null {
  if (!projectId) return null
  void invalidated.value
  return projects.get(projectId)?.block?.role ?? null
}

/** Non-reactive-friendly reads for plain modules (a click handler, a guard). */
export function systemCan(action: string): boolean {
  return decisionOf(systemBlock.value, action).allowed
}
export function systemDecision(action: string): CapabilityDecision {
  return decisionOf(systemBlock.value, action)
}
/** The /auth/me answer (with its system block) has arrived. */
export function systemCapabilitiesReady(): boolean {
  return systemBlock.value !== null
}
export function systemRole(): string | null {
  return systemBlock.value?.role ?? null
}
export function projectDecision(projectId: string | null | undefined, action: string): CapabilityDecision {
  if (!projectId) return NO_DECISION
  void invalidated.value
  const found = projects.get(projectId)
  if (!found) void loadProjectCapabilities(projectId)
  return decisionOf(found?.block, action)
}
export function projectCan(projectId: string | null | undefined, action: string): boolean {
  return projectDecision(projectId, action).allowed
}
