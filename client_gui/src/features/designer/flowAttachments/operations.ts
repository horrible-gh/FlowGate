// flowgate.v02test.0036 T#1 (R1) — the Flow attachment work manager. Pending work belongs to the signed-in session,
// the Project and the Flow, not to a window: a 🗑 staged in a Flow's attachment window keeps its five-second countdown
// and is sent when it runs out, whether the window is closed, unmounted, its Host remounted, the route left or another
// Flow opened. The windows, the Host and the Shell only draw this state and forward the user's presses.
//
// Work is dropped in two cases only: the session ends (sign-out, lost authentication, another user) and the Flow
// turns read-only (archived). A Flow that becomes writable again does not get the dropped work back — neither the
// groups still counting nor the files of a group already being sent that were not sent yet (cancel epoch).
//
// The archive state is watched by the manager itself, for as long as a Project has pending or in-flight work: it
// subscribes to that Project's `group_view_refresh` events (the same signal the Designer re-reads its archive tree on)
// and on each event or stream resync reads the archive state of the Flows with work. So a Flow archived after the user
// left the Designer or moved to another Project still drops its unsent deletes. The Designer's own archive state
// (connectReadOnly, from the Host) and the open window's read-only watch stay as extra, faster paths.
//
// Work key: (authSessionGeneration, owner, projectId, flowId). A token refresh is the same session; sign-out bumps the
// generation, so a response that arrives for an older generation changes nothing.
//
// Nothing here runs on import: startFlowAttachmentOperations() installs the session listeners once from the app start
// (app/main.ts) and disposeFlowAttachmentOperations() removes them (tests reset with it).
//
// Later cards hang off this manager (types only here, see FlowOperationsLater): 3 — page leave (flushPagehide),
// 4 — the upload queue (enqueueUpload / answerCollision), 5 — structured errors (errors / clearError).
import { effectScope, reactive, ref, shallowReactive, watch, type EffectScope } from 'vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import { AUTH_SESSION_ENDED_EVENT } from '@shared/authSessionSignal'
import { subscribeProjectEvents } from '@/features/project/projectEvents'
import { deleteFile, readFiles, type FlowFile } from './api'
import { createUndoQueue, type UndoGroup } from './undo'
import { createUploadWorker, type UploadState, type CollisionChoice } from './uploads'

export interface FlowScope { readonly projectId: string; readonly flowId: string }

/** A failure kept with the Flow. `text` is the server's message; null means "show the generic failure in the current language". */
export interface FlowOperationError { text: string | null }

/** Card 5 replaces the single `error` with a list of these (kind, target, operation id, order). Declared only. */
export interface FlowOperationErrorEntry { id: number; kind: 'delete' | 'upload' | 'read' | 'download'; text: string | null; at: number }

/** What a window draws for one Flow. Reactive; written only by this manager (and the window's own list reads). */
export interface FlowOperationsState extends UploadState {
  readonly key: string
  readonly projectId: string
  readonly flowId: string
  /** The server list as last read (a cache: a reopened window shows it at once, then reads again). */
  files: FlowFile[]
  /** Keys whose delete request is on the way: hidden from the list until it answers; a failure brings the row back. */
  deleting: Set<string>
  error: FlowOperationError | null
  loading: boolean
  loaded: boolean
  readOnly: boolean
  /** Bumped on every queue change so computed rows re-read the (non-reactive) undo queue. */
  revision: number
  /** Card 5: structured errors. Always empty until then. */
  errors: FlowOperationErrorEntry[]
}

/** Extension points of cards 3–5, declared so they attach to the same manager. Not implemented by this card. */
export interface FlowOperationsLater {
  /** Card 3: on pagehide send the pending deletes of writable, signed-in Flows at once (keepalive). */
  flushPagehide(): number
  /** Card 4: the upload queue of a Flow, kept while its window is closed. */
  enqueueUpload(scope: FlowScope, files: File[]): void
  /** Card 4: the answer to a name conflict question. */
  answerCollision(requestId: string, choice: 'cancel' | 'keep_both' | 'overwrite'): void
  /** Card 5: the close button of an error. */
  clearError(scope: FlowScope, errorId: number): boolean
}

/** Whether a Flow is archived for sure: true locks, false unlocks, null = not known (changes nothing). */
export type FlowReadOnlySource = (projectId: string, flowId: string) => boolean | null

/** What the manager-lifetime archive watch uses (the app's defaults; tests pass fakes). */
export interface FlowArchiveWatchDeps {
  /** Subscribes to a Project's server events; returns the unsubscribe. */
  subscribe(projectId: string, subscription: { events: readonly string[]; event(): void; resync?(): void }): () => void
  /** The server's archive state of a Flow (true = archived). A rejection means "not known" and changes nothing. */
  readArchived(flowId: string): Promise<boolean>
}

/** The event the server sends when a Flow's archive state (among other group view facts) changes. */
export const FLOW_ARCHIVE_EVENTS: readonly string[] = ['group_view_refresh']

const defaultArchiveDeps: FlowArchiveWatchDeps = {
  subscribe: (projectId, subscription) => subscribeProjectEvents(projectId, {
    events: subscription.events,
    event: () => subscription.event(),
    resync: subscription.resync ? () => subscription.resync!() : undefined,
  }),
  // Loaded on first use: the archive API pulls the Graph API in, which this manager does not need on import.
  readArchived: async flowId => (await (await import('@/features/project/flowArchiveApi')).readFlowArchive(flowId)).archived,
}

interface Entry {
  readonly key: string
  readonly generation: number
  readonly owner: string
  readonly projectId: string
  readonly flowId: string
  readonly state: FlowOperationsState
  readonly queue: ReturnType<typeof createUndoQueue<FlowFile>>
  readonly views: Set<string>
  uploads: ReturnType<typeof createUploadWorker>
  disposed: boolean
  readSequence: number
  /** Moves on every read-only lock: a group already being sent stops sending its files not sent yet, for good. */
  cancelEpoch: number
  /** Groups being sent now (the archive watch stays while a Project has any). */
  sending: number
  /** The archive read in progress for this Flow (a group about to send waits for it). */
  archiveCheck: Promise<void> | null
  archiveSequence: number
}

const generation = ref(0)
const entries = shallowReactive(new Map<string, Entry>())
let started: { scope: EffectScope; onEnded: () => void; deps: FlowArchiveWatchDeps } | null = null
/** Archive watches of the current session, by Project: kept while the Project has pending or in-flight work. */
const archiveWatches = new Map<string, () => void>()
let syncScheduled = false

const ownerNow = () => useCurrentUser().user.value?.username ?? ''
const keyOf = (gen: number, owner: string, scope: FlowScope) => [gen, owner, scope.projectId, scope.flowId].join('\u0001')
const failureOf = (e: unknown): FlowOperationError => {
  const raw = e as { response?: { data?: { error?: { message?: string } } } }
  return { text: raw?.response?.data?.error?.message || null }
}
/** True once the entry's session ended (or the manager was reset): its late answers must not touch any state. */
const stale = (entry: Entry) => entry.disposed || entry.generation !== generation.value

/** The Flow's entry of the current session if there is one. Never creates (safe inside computed rows). */
function peek(scope: FlowScope): Entry | undefined { return entries.get(keyOf(generation.value, ownerNow(), scope)) }

function entryFor(scope: FlowScope): Entry {
  const owner = ownerNow()
  const key = keyOf(generation.value, owner, scope)
  const found = entries.get(key)
  if (found) return found
  const state = reactive<FlowOperationsState>({
    key, projectId: scope.projectId, flowId: scope.flowId,
    files: [], deleting: new Set(), error: null, loading: false, loaded: false, readOnly: false, revision: 0, errors: [],
    currentFile: null, pendingCount: 0, collision: null, cancelledUploads: [], dirty: false, uploadRevision: 0,
  }) as FlowOperationsState
  // The queue holds timers and file snapshots; it stays out of Vue's proxies (`revision` tells the views).
  const holder: { entry?: Entry } = {}
  const queue = createUndoQueue<FlowFile>(items => commit(holder.entry!, items), () => { state.revision++; scheduleArchiveSync() })
  const entry: Entry = {
    key, generation: generation.value, owner, projectId: scope.projectId, flowId: scope.flowId, state, queue, views: new Set(),
    uploads: createUploadWorker({
      flowId: scope.flowId, state,
      stale: () => stale(holder.entry!),
      readOnly: () => state.readOnly,
      hasView: () => holder.entry!.views.size > 0,
      ready: async () => {
        const entry = holder.entry!
        while (entry.archiveCheck && !stale(entry) && !state.readOnly) await entry.archiveCheck
      },
      changed: scheduleArchiveSync,
      completed: () => {
        state.dirty = true
        state.uploadRevision++
        if (holder.entry!.views.size) void refreshEntry(holder.entry!)
      },
      failed: error => { state.error = failureOf(error) },
    }),
    disposed: false, readSequence: 0, cancelEpoch: 0, sending: 0, archiveCheck: null, archiveSequence: 0,
  }
  holder.entry = entry
  entries.set(key, entry)
  return entry
}

/**
 * The timer ran out: delete the group's files one by one. Uses only the immutable scope, the file snapshots taken
 * when 🗑 was pressed and the Flow state — no window, props, emit or locale. Before each request and after each answer
 * it checks that the session is the same; before each request it also waits for an archive read in progress and
 * checks that the Flow was not locked since the group started (cancel epoch). A file not yet sent when the Flow turned
 * read-only is never sent, even if the Flow is writable again before the earlier answer arrives (its row comes back).
 */
async function commit(entry: Entry, items: FlowFile[]) {
  const { state } = entry
  if (stale(entry)) return
  const epoch = entry.cancelEpoch
  const cancelled = () => entry.cancelEpoch !== epoch || state.readOnly
  entry.sending++
  try {
    for (const file of items) state.deleting.add(file.key)
    state.revision++
    for (const file of items) {
      if (stale(entry)) return
      // An archive event came in: wait for its read before sending (it may lock the Flow).
      while (entry.archiveCheck && !cancelled()) {
        await entry.archiveCheck
        if (stale(entry)) return
      }
      if (cancelled()) { state.deleting.delete(file.key); state.revision++; continue }
      try {
        await deleteFile(entry.flowId, file)
        if (stale(entry)) return
        state.files = state.files.filter(f => f.key !== file.key)
      } catch (e) {
        if (stale(entry)) return
        state.error = failureOf(e)
      } finally {
        if (!stale(entry)) { state.deleting.delete(file.key); state.revision++ }
      }
    }
    // Same rule as the window had (R4 — reading again only after this run's failure — is card 5's).
    if (!stale(entry) && state.error) await refreshEntry(entry)
  } finally {
    entry.sending--
    scheduleArchiveSync()
  }
}

async function refreshEntry(entry: Entry) {
  const { state } = entry
  const seq = ++entry.readSequence
  const uploadRevision = state.uploadRevision
  state.loading = true
  try {
    const data = await readFiles(entry.flowId)
    if (stale(entry) || seq !== entry.readSequence) return
    state.files = data
    state.loaded = true
    if (uploadRevision === state.uploadRevision) state.dirty = false
  } catch (e) {
    if (!stale(entry) && seq === entry.readSequence) state.error = failureOf(e)
  } finally {
    if (!stale(entry) && seq === entry.readSequence) state.loading = false
  }
}

function setEntryReadOnly(entry: Entry, locked: boolean) {
  entry.state.readOnly = locked
  // The Flow cannot be written: the deletes not sent yet are dropped and do not come back when it unlocks —
  // the groups still counting (clear) and the rest of a group already being sent (cancel epoch).
  if (locked) {
    entry.cancelEpoch++
    entry.uploads.cancel()
    entry.queue.clear()
  }
}

/* ── manager-lifetime archive watch ── */

/** Whether the Flow has work the archive state still matters for. */
const busy = (entry: Entry) => !stale(entry) && (entry.queue.groups.length > 0 || entry.sending > 0 || entry.uploads.busy())

/** Coalesced: a group that runs out leaves the queue just before it starts sending, which must not drop the watch. */
function scheduleArchiveSync() {
  if (syncScheduled) return
  syncScheduled = true
  void Promise.resolve().then(() => { syncScheduled = false; syncArchiveWatches() })
}

/** One watch per Project with work while the manager runs; a Project without work any more stops its watch. */
function syncArchiveWatches() {
  const deps = started?.deps
  const wanted = new Set<string>()
  if (deps) for (const entry of entries.values()) if (busy(entry)) wanted.add(entry.projectId)
  for (const [projectId, stop] of [...archiveWatches]) {
    if (!wanted.has(projectId)) { archiveWatches.delete(projectId); stop() }
  }
  if (!deps) return
  for (const projectId of wanted) {
    if (archiveWatches.has(projectId)) continue
    const check = () => checkProjectArchive(projectId)
    archiveWatches.set(projectId, deps.subscribe(projectId, { events: FLOW_ARCHIVE_EVENTS, event: check, resync: check }))
  }
}

/** An archive event (or a stream resync) for a Project: read the archive state of each of its Flows with work. */
function checkProjectArchive(projectId: string) {
  const deps = started?.deps
  if (!deps) return
  for (const entry of entries.values()) {
    if (entry.projectId !== projectId || !busy(entry)) continue
    const seq = ++entry.archiveSequence
    const run: Promise<void> = deps.readArchived(entry.flowId).then(
      archived => {
        if (stale(entry) || seq !== entry.archiveSequence || typeof archived !== 'boolean') return
        if (entry.state.readOnly !== archived) setEntryReadOnly(entry, archived)
      },
      () => { /* Not known: changes nothing (the server's guard still refuses a delete on an archived Flow). */ },
    ).finally(() => { if (entry.archiveCheck === run) entry.archiveCheck = null })
    entry.archiveCheck = run
  }
}

/* ── public API ── */

/** The Flow's state for the current session (made on first use). */
export function getFlowState(scope: FlowScope): FlowOperationsState { return entryFor(scope).state }

/** The session generation (reactive): a window re-attaches when it changes. */
export function sessionGeneration(): number { return generation.value }

/** A window shows this Flow. Returns its state. Showing never resets a countdown. */
export function attachView(scope: FlowScope, viewId: string): FlowOperationsState {
  const entry = entryFor(scope)
  entry.views.add(viewId)
  return entry.state
}

/** The window is gone. Its pending work goes on. */
export function detachView(scope: FlowScope, viewId: string): void {
  for (const entry of entries.values()) {
    if (entry.projectId === scope.projectId && entry.flowId === scope.flowId) {
      entry.views.delete(viewId)
      if (!entry.views.size) entry.uploads.detach()
    }
  }
}

/** Enqueues against the captured session/project/Flow, independent of any window. */
export function enqueueUpload(scope: FlowScope, files: readonly File[]): void { entryFor(scope).uploads.enqueue(files) }
export function answerCollision(requestId: string, choice: CollisionChoice): void {
  for (const entry of entries.values()) {
    if (!stale(entry) && entry.state.collision?.id === requestId) { entry.uploads.respond(requestId, choice); return }
  }
}

/** Whether a window shows this Flow now. */
export function hasView(scope: FlowScope): boolean { return (peek(scope)?.views.size ?? 0) > 0 }

/** One 🗑 press (a row or the selection) = one group with its own five seconds. A file already staged is not staged twice. */
export function stageDelete(scope: FlowScope, files: readonly FlowFile[]): void {
  const entry = entryFor(scope)
  entry.queue.stage(files.map(f => ({ ...f })))
}
/** A row's ↶: that file only; the rest of its group keeps counting. */
export function undoItem(scope: FlowScope, key: string): void { peek(scope)?.queue.undoItem(key) }
/** The numbered ↶: the given group (the latest of more than one file when omitted). */
export function undoGroup(scope: FlowScope, group?: UndoGroup<FlowFile>): boolean {
  const entry = peek(scope)
  return entry ? entry.queue.undoGroup(group ?? entry.queue.bulk()) : false
}
/** Ctrl+Z in the focused window: the latest group of that window's Flow only. */
export function undoLast(scope: FlowScope): boolean { return peek(scope)?.queue.undoLast() ?? false }
/** The latest group of more than one file (the numbered ↶). */
export function bulkGroup(scope: FlowScope): UndoGroup<FlowFile> | undefined { return peek(scope)?.queue.bulk() }
/** The pending group of a file (its start drives the ring), if any. */
export function pendingGroup(scope: FlowScope, key: string): UndoGroup<FlowFile> | undefined { return peek(scope)?.queue.pending(key) }
/** Snapshots of every file waiting for its five seconds, so a row that left the server list keeps its ↶. */
export function pendingFiles(scope: FlowScope): FlowFile[] { return peek(scope)?.queue.groups.flatMap(g => g.items) ?? [] }

/** Reads the Flow's files again into its state. */
export function refresh(scope: FlowScope): Promise<void> { return refreshEntry(entryFor(scope)) }

/** The Flow turned read-only (true: drop its unsent deletes) or writable again (false: nothing comes back). */
export function setReadOnly(scope: FlowScope, locked: boolean): void { setEntryReadOnly(entryFor(scope), locked) }

/**
 * The archive state from outside the windows (the Designer's Flow archive state). While connected, every Flow the
 * manager holds follows it: true locks and drops unsent deletes, false unlocks, null changes nothing. Returns the
 * disconnect.
 */
export function connectReadOnly(source: FlowReadOnlySource): () => void {
  const scope = effectScope(true)
  scope.run(() => {
    watch(
      () => [...entries.values()].filter(e => !stale(e)).map(e => [e.key, source(e.projectId, e.flowId)] as const),
      pairs => {
        for (const [key, locked] of pairs) {
          const entry = entries.get(key)
          if (!entry || stale(entry) || typeof locked !== 'boolean' || entry.state.readOnly === locked) continue
          setEntryReadOnly(entry, locked)
        }
      },
      { immediate: true },
    )
  })
  return () => scope.stop()
}

/**
 * The session ended (sign-out, lost authentication, sign-out in another tab, another user): every unsent delete and
 * its file references are dropped, and the generation moves on so late answers are ignored. A request the server has
 * already accepted cannot be taken back.
 */
export function stopSession(): void {
  for (const entry of entries.values()) {
    entry.queue.clear()
    entry.disposed = true
    entry.uploads.dispose()
    entry.archiveCheck = null
    entry.views.clear()
  }
  entries.clear()
  for (const stop of archiveWatches.values()) stop()
  archiveWatches.clear()
  generation.value++
}

/** The Projects whose archive state the manager watches now (for tests and diagnostics). */
export function watchedProjects(): string[] { return [...archiveWatches.keys()] }

/**
 * Installs the session listeners and the archive watch once (app start). Idempotent; returns the dispose. `deps`
 * replaces the archive watch's event subscription and archive read (tests); the app passes nothing.
 */
export function startFlowAttachmentOperations(deps: Partial<FlowArchiveWatchDeps> = {}): () => void {
  if (started) return disposeFlowAttachmentOperations
  const onEnded = () => stopSession()
  const scope = effectScope(true)
  scope.run(() => {
    // Another user signed in (or the user went away) without a sign-out through logout(): a new session.
    watch(ownerNow, (now, before) => { if (before && now !== before) stopSession() })
  })
  if (typeof window !== 'undefined') window.addEventListener(AUTH_SESSION_ENDED_EVENT, onEnded)
  started = { scope, onEnded, deps: { ...defaultArchiveDeps, ...deps } }
  syncArchiveWatches()
  return disposeFlowAttachmentOperations
}

/** Removes the listeners and drops all work (tests reset with this between cases). */
export function disposeFlowAttachmentOperations(): void {
  if (started) {
    started.scope.stop()
    if (typeof window !== 'undefined') window.removeEventListener(AUTH_SESSION_ENDED_EVENT, started.onEnded)
    started = null
  }
  stopSession()
}
