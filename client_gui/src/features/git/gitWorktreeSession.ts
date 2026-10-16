// flowgate.v02.0066 — Group worktree session: the single owner of the Git tab's worktree area
// (0066 D#2 §2-2). Not a Pinia store: DesignerGitPanel creates one beside its 0033 Git session and
// both live and die with the panel. Kept apart from gitSession on purpose — the Base checkout card
// (commit / revertAll / commitMessage there) and the Group worktree never share a state (§2-1).
//
// It only reads the 0033 session (context, read-only, git/status, busy, the group card) and never
// changes it. Every server call goes through GitSource.worktree (E1–E12); the screen changes only
// from what an answer says, and an answer applies only while it is still the one asked for:
//   ① a generation per project / Flow / gate opening drops every older answer — reads and
//     writes alike, a success as much as a refusal: a late answer of another context changes
//     no state, notice, selection, message or dependent read,
//   ② E1 reads and E2–E6 writes share one sequence: an older status never covers a newer one,
//   ③ reads asked for while a write runs are held and sent once after it,
//   ④ E8 answers are tied to the target and the number they were asked with.
// A new status fingerprint (worktree token or HEAD) makes what depends on it read again (§3-2-3).
// A confirm dialog is bound to the context it was opened in (a ticket): it is dropped when that
// context goes and never sends to another one.
import { computed, effectScope, inject, reactive, readonly, ref, watch, type ComputedRef, type DeepReadonly, type InjectionKey } from 'vue'
import { worktreeErrorOf } from './api'
import { createHttpGitSource, type GitSource, type GitWorktreeSource } from './gitSource'
import type { GitConfirmRequest, GitSession } from './gitSession'
import type {
  GitCenterDiffRefresh,
  ManualMergeTarget,
  ManualMergeView,
  MergeResultsView,
  WorktreeFile,
  WorktreeNotice,
  WorktreeSelection,
  WorktreeStatus,
  WorktreeStatusAfter,
} from './types'
import {
  buildSections,
  hiddenStagedCount,
  isSelectable,
  pruneSelection,
  selectAllTargets,
  stagedProtectedRows,
  stageTargets,
  unstageTargets,
  WORKTREE_FOLDER_PAGE,
  type WorktreeFilter,
  type WorktreeFolderMode,
  type WorktreeSectionView,
} from './worktreeList'
import { worktreeReasons, type WorktreeButton, type WorktreeReason } from './worktreeReasons'

/** The server's normalized commit subject maximum (COMMIT_SUBJECT_MAX); the server decides. */
export const WORKTREE_MESSAGE_MAX = 200

type Load = 'idle' | 'loading' | 'ready' | 'error'

/** Where a notice line is drawn: under the changed-files header, or under the commit line. */
export type WorktreeNoticeArea = 'files' | 'commit'

// Refusal codes with their own notice text (gui.git.worktree.notice.*); others read "failed".
export const WORKTREE_NOTICE_CODES: readonly string[] = [
  'worktree_changed',
  'path_protected',
  'path_not_changed',
  'new_path_use_remove',
  'existing_path_use_revert',
  'staged_protected_paths',
  'index_entry_unsupported',
  'commit_message_required',
  'invalid_request',
  'nothing_staged',
  'suggestion_in_progress',
  'ai_timeout',
  'ai_failed',
  'ai_invalid_response',
  'git_busy',
  'invalid_state',
  'ownership_unavailable',
]

// Refusals after which the list on screen may be wrong: E1 is read again (never the request).
const REREAD_CODES: readonly string[] = [
  'path_not_changed',
  'new_path_use_remove',
  'existing_path_use_revert',
  'nothing_staged',
]

const SESSION_CARDS: readonly string[] = ['conflict', 'ai', 'review', 'unsupported']

export interface WorktreeSuggestion {
  // The staged snapshot the draft was made from.
  indexToken: string | null
  inputTruncated: boolean
}

export interface WorktreeState {
  status: WorktreeStatus | null
  statusLoad: Load
  // Why the last E1 read failed: network keeps the last answer, forbidden / failed show a line.
  statusError: 'network' | 'forbidden' | 'failed' | null
  // E8 — the judgment for `mergeTarget` (null = not read, or voided until the new one comes).
  merge: ManualMergeView | null
  mergeTarget: string | null
  // The target list survives a voided judgment so the selector stays.
  mergeTargets: ManualMergeTarget[]
  mergeLoad: Load
  // E10
  results: MergeResultsView | null
  resultsLoad: Load
  // The default AI of the project (§3-5 rank 12); null = none or not read.
  providerId: string | null
  // The one worktree action in flight (stage / unstage / revert / remove / commit / suggest).
  busy: string | null
  // A 403 on a worktree request: every write stays disabled for the session.
  forbidden: boolean
  filesOpen: boolean
  searchOpen: boolean
  menuOpen: boolean
  lockOpen: boolean
  search: string
  filter: WorktreeFilter
  folderMode: WorktreeFolderMode
  folderToggles: Record<string, boolean>
  limits: Record<string, number>
  selected: string[]
  message: string
  suggestion: WorktreeSuggestion | null
  notices: Partial<Record<WorktreeNoticeArea, WorktreeNotice>>
}

/** The context an action was asked in: project, Flow group and the session generation (①). */
export interface WorktreeTicket {
  projectId: string
  groupId: string
  generation: number
}

/**
 * What a commit confirm dialog showed: its count and the E1 index token sent with the commit,
 * and the context it was opened in.
 */
export interface WorktreeCommitSnapshot {
  count: number
  indexToken: string | null
  message: string
  ticket: WorktreeTicket
}

export interface GitWorktreeSession {
  readonly state: DeepReadonly<WorktreeState>
  /** §3-1 row 6: Git is confirmed on, a Flow is active and the source has a worktree. */
  readonly gateOpen: ComputedRef<boolean>
  readonly sections: ComputedRef<WorktreeSectionView[]>
  readonly hiddenStaged: ComputedRef<number>
  readonly reasons: ComputedRef<Record<WorktreeButton, WorktreeReason>>
  /** The draft was made from another Stage than the one shown now. */
  readonly suggestionStale: ComputedRef<boolean>
  /** `counts.changed` for the tab badge; null while the area is hidden or unread (§3-8). */
  readonly changedCount: ComputedRef<number | null>

  refresh(): void
  /** The current context while the gate is open; null otherwise. */
  ticket(): WorktreeTicket | null
  /** Still the same project, Flow and generation, gate open, not disposed (reactive). */
  isCurrent(ticket: WorktreeTicket): boolean
  toggleFiles(): void
  toggleSearch(): void
  toggleMenu(): void
  toggleLock(): void
  setSearch(search: string): void
  setFilter(filter: WorktreeFilter): void
  setFolderMode(mode: WorktreeFolderMode): void
  toggleFolder(folder: string, open: boolean): void
  showMore(key: string): void
  setSelected(path: string, selected: boolean): void
  toggleSelectAll(): void
  clearSelection(): void

  stagePath(path: string): Promise<void>
  unstagePath(path: string): Promise<void>
  stageSelected(): Promise<void>
  unstageSelected(): Promise<void>
  stageAll(): Promise<void>
  unstageAll(): Promise<void>
  /** With a ticket (a confirm dialog), nothing is sent unless it is still current. */
  revert(path: string, ticket?: WorktreeTicket | null): Promise<void>
  remove(path: string, ticket?: WorktreeTicket | null): Promise<void>
  setMessage(message: string): void
  commitSnapshot(): WorktreeCommitSnapshot | null
  commit(snapshot: WorktreeCommitSnapshot): Promise<void>
  suggest(ticket?: WorktreeTicket | null): Promise<void>
  dismissNotice(area: WorktreeNoticeArea): void
  dispose(): void
}

export interface GitWorktreeSessionOptions {
  git: GitSession
  source?: GitSource
  /** `git-center-diff-refresh`: the panel raises it to the Center Diff (§2-4). */
  onCenterDiffRefresh?: (request: GitCenterDiffRefresh) => void
}

const fingerprint = (status: WorktreeStatus | null) => (status ? `${status.worktreeToken ?? ''}|${status.head ?? ''}` : null)

export function createGitWorktreeSession(options: GitWorktreeSessionOptions): GitWorktreeSession {
  const git = options.git
  let source = options.source ?? null
  const src = (): GitSource => (source ??= createHttpGitSource())
  const wsrc = (): GitWorktreeSource | null => src().worktree ?? null

  const state = reactive<WorktreeState>({
    status: null,
    statusLoad: 'idle',
    statusError: null,
    merge: null,
    mergeTarget: null,
    mergeTargets: [],
    mergeLoad: 'idle',
    results: null,
    resultsLoad: 'idle',
    providerId: null,
    busy: null,
    forbidden: false,
    filesOpen: false,
    searchOpen: false,
    menuOpen: false,
    lockOpen: false,
    search: '',
    filter: 'all',
    folderMode: 'default',
    folderToggles: {},
    limits: {},
    selected: [],
    message: '',
    suggestion: null,
    notices: {},
  })

  let disposed = false
  // ① Bumped per project / Flow switch, whenever the gate closes and on dispose. `generation`
  // mirrors it so a held ticket (an open dialog) sees the change.
  let gen = 0
  const generation = ref(0)
  function nextGeneration(): void {
    gen += 1
    generation.value = gen
  }
  // ② One sequence for E1 reads and E2–E6 writes; the status of an older number never applies.
  let e1Seq = 0
  let appliedSeq = 0
  // ④ The latest E8 / E10 request; only its answer applies.
  let e8Seq = 0
  let e10Seq = 0
  // A merge target a person chose (the first default-target answer must not override it).
  let targetChosen = false
  let providersFor: string | null = null
  // ③ Reads asked for while a write runs, sent once afterwards (and coalesced otherwise).
  const pendingReads = { e1: false, e8: false, e10: false }
  let flushQueued = false

  const projectId = () => git.state.projectId
  const groupId = () => git.state.flow?.groupId ?? null

  // ── Gate (§3-1) ────────────────────────────────────────────────────────────────────

  const gateOpen = computed(() => {
    const g = git.state
    if (g.statusLoad !== 'ready' || !g.status || g.status.enabled !== true) return false
    if (!g.projectId || !g.flow?.groupId) return false
    return !!wsrc()
  })

  // ── Derived views ──────────────────────────────────────────────────────────────────

  const sections = computed(() =>
    buildSections(state.status as WorktreeStatus | null, {
      search: state.search,
      filter: state.filter,
      folderMode: state.folderMode,
      folderToggles: state.folderToggles,
      limits: state.limits,
    }),
  )

  const hiddenStaged = computed(() => hiddenStagedCount(state.status as WorktreeStatus | null))

  const reasons = computed(() =>
    worktreeReasons({
      readOnly: git.state.readOnly,
      forbidden: state.forbidden,
      busy: !!state.busy || !!git.state.busy,
      status: state.status as WorktreeStatus | null,
      sessionCard: SESSION_CARDS.includes(git.groupCard.value ?? ''),
      hiddenStaged: hiddenStaged.value,
      stagedProtectedRows: stagedProtectedRows(state.status as WorktreeStatus | null),
      message: state.message,
      providerId: state.providerId,
    }),
  )

  const suggestionStale = computed(
    () => !!state.suggestion && !!state.status && state.suggestion.indexToken !== state.status.indexToken,
  )

  const changedCount = computed(() => {
    const status = state.status
    if (!gateOpen.value || !status?.worktreeAvailable) return null
    return status.counts.changed
  })

  // ── Notices ────────────────────────────────────────────────────────────────────────

  function setNotice(area: WorktreeNoticeArea, notice: WorktreeNotice): void {
    state.notices = { ...state.notices, [area]: notice }
  }

  function dismissNotice(area: WorktreeNoticeArea): void {
    if (!(area in state.notices)) return
    const next = { ...state.notices }
    delete next[area]
    state.notices = next
  }

  // A refusal shows its code (never the server text); a 403 locks every write (§3-5 rank 2).
  function reportError(area: WorktreeNoticeArea, error: unknown): void {
    const err = worktreeErrorOf(error)
    if (err.network) return setNotice(area, { code: 'network', tone: 'error' })
    if (err.httpStatus === 403 || err.code === 'forbidden') {
      state.forbidden = true
      return setNotice(area, { code: 'forbidden', tone: 'error' })
    }
    const code = err.code ?? ''
    const known = WORKTREE_NOTICE_CODES.includes(code)
    const notice: WorktreeNotice = known
      ? { code, tone: 'error' }
      : { code: 'failed', tone: 'error', params: { code: code || String(err.httpStatus ?? '') } }
    if (code === 'staged_protected_paths') notice.link = 'unstage_all'
    setNotice(area, notice)
    if (err.httpStatus === 409 || REREAD_CODES.includes(code)) requestReads({ e1: true })
  }

  // ── Context and gate ───────────────────────────────────────────────────────────────

  function clearServer(): void {
    nextGeneration()
    appliedSeq = 0
    pendingReads.e1 = pendingReads.e8 = pendingReads.e10 = false
    state.status = null
    state.statusLoad = 'idle'
    state.statusError = null
    state.merge = null
    state.mergeTargets = []
    state.mergeLoad = 'idle'
    state.results = null
    state.resultsLoad = 'idle'
    state.busy = null
    state.selected = []
    state.notices = {}
  }

  // A project / Flow switch: every screen state goes back to its first look (§3-2-1).
  function resetAll(projectChanged: boolean): void {
    clearServer()
    state.filesOpen = false
    state.searchOpen = false
    state.menuOpen = false
    state.lockOpen = false
    state.search = ''
    state.filter = 'all'
    state.folderMode = 'default'
    state.folderToggles = {}
    state.limits = {}
    state.message = ''
    state.suggestion = null
    state.mergeTarget = null
    targetChosen = false
    if (projectChanged) {
      state.forbidden = false
      state.providerId = null
      providersFor = null
    }
  }

  // ── Reads ──────────────────────────────────────────────────────────────────────────

  // Applies an E1 status of sequence `seq` unless a newer one is already on screen (②).
  function applyStatus(status: WorktreeStatus, seq: number): boolean {
    if (seq < appliedSeq) return false
    appliedSeq = seq
    const before = fingerprint(state.status as WorktreeStatus | null)
    state.status = status
    state.statusLoad = 'ready'
    state.statusError = null
    state.selected = pruneSelection(state.selected, status)
    const after = fingerprint(status)
    if (before !== null && before !== after) dependentsChanged()
    return true
  }

  // §3-2-3: the merge judgment of the chosen target is void until it is read again; an open
  // Center Diff of this group reads again. E10 does not depend on the workspace.
  function dependentsChanged(): void {
    const gid = groupId()
    state.merge = null
    if (state.mergeLoad !== 'idle') state.mergeLoad = 'loading'
    requestReads({ e8: true })
    if (gid) options.onCenterDiffRefresh?.({ groupId: gid, closeCompare: false })
  }

  // A worktree write answered: its refreshed status applies, or E1 is read again (status_error).
  function applyAfter(result: WorktreeStatusAfter, seq: number): void {
    if (result.status) applyStatus(result.status, seq)
    else requestReads({ e1: true })
  }

  async function readStatus(): Promise<void> {
    const wt = wsrc()
    const gid = groupId()
    if (!wt || !gid || disposed) return
    const myGen = gen
    const seq = ++e1Seq
    if (!state.status) state.statusLoad = 'loading'
    try {
      const status = await wt.loadStatus(gid)
      if (myGen !== gen || disposed) return
      applyStatus(status, seq)
    } catch (error) {
      if (myGen !== gen || disposed || seq < appliedSeq) return
      const err = worktreeErrorOf(error)
      if (err.network && state.status) {
        // Keep the last answer; the area says it could not read again (§3-2-1).
        state.statusError = 'network'
        return
      }
      if (err.httpStatus === 403 || err.code === 'forbidden') state.forbidden = true
      state.statusError = err.network ? 'network' : err.httpStatus === 403 || err.code === 'forbidden' ? 'forbidden' : 'failed'
      if (state.statusError === 'failed') {
        state.status = null
        state.selected = []
      }
      state.statusLoad = state.status ? 'ready' : 'error'
    }
  }

  async function readMerge(): Promise<void> {
    const wt = wsrc()
    const gid = groupId()
    if (!wt || !gid || disposed) return
    const myGen = gen
    const seq = ++e8Seq
    // null = the default target (work base); its answer then picks the selection.
    const target = state.mergeTarget
    state.mergeLoad = 'loading'
    try {
      const view = await wt.loadManualMerge(gid, target)
      if (myGen !== gen || disposed || seq !== e8Seq || target !== state.mergeTarget) return
      if (target === null) {
        if (targetChosen) return
        const chosen = view.targets.find((t) => t.name === view.defaultTarget && t.eligible)
        state.mergeTarget = chosen ? chosen.name : null
      }
      state.merge = view
      state.mergeTargets = view.targets
      state.mergeLoad = 'ready'
    } catch {
      if (myGen !== gen || disposed || seq !== e8Seq || target !== state.mergeTarget) return
      state.merge = null
      state.mergeLoad = 'error'
    }
  }

  async function readResults(): Promise<void> {
    const wt = wsrc()
    const gid = groupId()
    if (!wt || !gid || disposed) return
    const myGen = gen
    const seq = ++e10Seq
    if (!state.results) state.resultsLoad = 'loading'
    try {
      const results = await wt.loadMergeResults(gid)
      if (myGen !== gen || disposed || seq !== e10Seq) return
      state.results = results
      state.resultsLoad = 'ready'
    } catch {
      if (myGen !== gen || disposed || seq !== e10Seq) return
      state.resultsLoad = 'error'
    }
  }

  async function loadProviders(): Promise<void> {
    const pid = projectId()
    if (!pid || providersFor === pid) return
    providersFor = pid
    try {
      const providers = await src().fetchProviders(pid)
      if (disposed || projectId() !== pid) return
      const id = providers.defaultProviderId
      state.providerId = id && providers.providerIds.includes(id) ? id : null
    } catch {
      // No list = no default AI (rank 12); asked again on the next gate opening.
      if (projectId() === pid) providersFor = null
    }
  }

  function flushReads(): void {
    flushQueued = false
    if (disposed || state.busy) return
    if (!gateOpen.value) {
      pendingReads.e1 = pendingReads.e8 = pendingReads.e10 = false
      return
    }
    const reads = { ...pendingReads }
    pendingReads.e1 = pendingReads.e8 = pendingReads.e10 = false
    if (reads.e1) void readStatus()
    if (reads.e8) void readMerge()
    if (reads.e10) void readResults()
  }

  // Reads asked for in one turn go out once; during a write they wait for its answer (③).
  function requestReads(reads: { e1?: boolean; e8?: boolean; e10?: boolean }): void {
    if (disposed) return
    pendingReads.e1 ||= !!reads.e1
    pendingReads.e8 ||= !!reads.e8
    pendingReads.e10 ||= !!reads.e10
    if (state.busy || flushQueued) return
    flushQueued = true
    void Promise.resolve().then(flushReads)
  }

  function refresh(): void {
    if (gateOpen.value) requestReads({ e1: true, e8: true, e10: true })
  }

  // ── Tickets: the context a dialog was opened in ────────────────────────────────────

  function ticket(): WorktreeTicket | null {
    const pid = projectId()
    const gid = groupId()
    if (disposed || !gateOpen.value || !pid || !gid) return null
    return { projectId: pid, groupId: gid, generation: generation.value }
  }

  function isCurrent(held: WorktreeTicket): boolean {
    return (
      generation.value === held.generation &&
      gateOpen.value &&
      !disposed &&
      projectId() === held.projectId &&
      groupId() === held.groupId
    )
  }

  // ── Watchers: context, gate and the 0033 session ───────────────────────────────────

  const scope = effectScope(true)
  scope.run(() => {
    let lastProject: string | null = null
    watch(
      () => [projectId(), groupId(), gateOpen.value] as const,
      ([pid, gid, open], previous) => {
        const [prevPid, prevGid, prevOpen] = previous ?? [null, null, false]
        const contextChanged = pid !== prevPid || gid !== prevGid
        if (contextChanged) resetAll(pid !== lastProject)
        else if (prevOpen && !open) clearServer()
        lastProject = pid
        if (open && (contextChanged || !prevOpen)) {
          requestReads({ e1: true, e8: true, e10: true })
          void loadProviders()
        }
      },
      { immediate: true },
    )
    // A 0033 action ended (busy back to null): the workspace may have changed (§3-2-1).
    watch(
      () => git.state.busy,
      (busy, previous) => {
        if (previous && !busy) refresh()
      },
    )
    // The group's pending row (0033 polling) changed its state: read once more.
    watch(
      () => {
        const row = git.pendingRow.value
        return row ? `${row.status}|${row.reviewState ?? ''}|${row.mergeId ?? ''}` : ''
      },
      () => refresh(),
    )
  })

  // ── Screen state ───────────────────────────────────────────────────────────────────

  function toggleFiles(): void {
    state.filesOpen = !state.filesOpen
    state.menuOpen = false
    // Folding the list closes search / filter and clears them; the selection stays (§3-7).
    if (!state.filesOpen) {
      state.searchOpen = false
      state.search = ''
      state.filter = 'all'
    }
  }

  function toggleSearch(): void {
    state.searchOpen = !state.searchOpen
    if (!state.searchOpen) {
      state.search = ''
      state.filter = 'all'
    }
  }

  function toggleMenu(): void {
    state.menuOpen = !state.menuOpen
  }

  function toggleLock(): void {
    state.lockOpen = !state.lockOpen
  }

  function setSearch(search: string): void {
    state.search = search
  }

  function setFilter(filter: WorktreeFilter): void {
    state.filter = filter
  }

  function setFolderMode(mode: WorktreeFolderMode): void {
    state.folderMode = mode
    state.folderToggles = {}
    state.menuOpen = false
  }

  // By folder path: the same folder folds in both sections.
  function toggleFolder(folder: string, open: boolean): void {
    state.folderToggles = { ...state.folderToggles, [folder]: !open }
  }

  function showMore(key: string): void {
    state.limits = { ...state.limits, [key]: (state.limits[key] ?? WORKTREE_FOLDER_PAGE) + WORKTREE_FOLDER_PAGE }
  }

  function setSelected(path: string, selected: boolean): void {
    const file = state.status?.files.find((f) => f.path === path)
    if (selected) {
      if (!file || !isSelectable(file as WorktreeFile) || state.selected.includes(path)) return
      state.selected = [...state.selected, path]
    } else {
      state.selected = state.selected.filter((p) => p !== path)
    }
  }

  // `전체 선택` checks what matches the search and filter; a second press clears those again.
  function toggleSelectAll(): void {
    const targets = selectAllTargets(state.status as WorktreeStatus | null, state.search, state.filter)
    if (!targets.length) return
    const all = targets.every((p) => state.selected.includes(p))
    state.selected = all
      ? state.selected.filter((p) => !targets.includes(p))
      : [...state.selected, ...targets.filter((p) => !state.selected.includes(p))]
  }

  function clearSelection(): void {
    state.selected = []
  }

  function setMessage(message: string): void {
    state.message = message.slice(0, WORKTREE_MESSAGE_MAX)
  }

  // ── Actions ────────────────────────────────────────────────────────────────────────

  // One worktree action at a time; no write while the 0033 session runs one either (§3-3).
  // `send` only asks the server; `apply` is what its answer changes on screen. An answer that
  // comes back after a project / Flow switch, a closed gate or dispose (another generation, ①)
  // is not applied: no status, notice, selection, message, draft or dependent read follows it.
  // A ticket (a confirm dialog) must still be current, or nothing is sent at all.
  async function write<T>(
    area: WorktreeNoticeArea,
    key: string,
    send: (wt: GitWorktreeSource, gid: string) => Promise<T>,
    apply: (result: T, seq: number) => void,
    held?: WorktreeTicket | null,
  ): Promise<void> {
    const wt = wsrc()
    const gid = groupId()
    if (!wt || !gid || disposed || !gateOpen.value || state.busy || git.state.busy) return
    if (held && !isCurrent(held)) return
    const myGen = gen
    const current = () => myGen === gen && !disposed
    const seq = ++e1Seq
    state.busy = key
    dismissNotice(area)
    try {
      const result = await send(wt, gid)
      if (current()) apply(result, seq)
    } catch (error) {
      if (current()) reportError(area, error)
    } finally {
      if (myGen === gen) state.busy = null
    }
    if (current()) flushReads()
  }

  const token = () => state.status?.worktreeToken ?? null

  function changeIndex(unstage: boolean, selection: WorktreeSelection, done: () => void): Promise<void> {
    return write(
      'files',
      unstage ? 'unstage' : 'stage',
      (wt, gid) => (unstage ? wt.unstage(gid, selection, token()) : wt.stage(gid, selection, token())),
      (result, seq) => {
        applyAfter(result, seq)
        done()
        if ('all' in selection && !unstage) {
          const s = result.skipped
          const skipped = s.managed + s.hidden + s.excludedNew + s.unsupported
          if (skipped > 0) setNotice('files', { code: 'skipped', params: { n: skipped } })
        }
        if ('all' in selection && unstage && result.appliedHiddenCount > 0) {
          setNotice('files', { code: 'hidden_released', params: { n: result.appliedHiddenCount } })
        }
        if (!result.status) setNotice('files', { code: 'status_reread' })
      },
    )
  }

  const allowed = (button: WorktreeButton) => !reasons.value[button].disabled
  const rowOf = (path: string) => state.status?.files.find((f) => f.path === path) ?? null
  const deselect = (paths: readonly string[]) => {
    state.selected = state.selected.filter((p) => !paths.includes(p))
  }

  function stagePath(path: string): Promise<void> {
    if (!allowed('stage') || !rowOf(path)?.canStage) return Promise.resolve()
    return changeIndex(false, { paths: [path] }, () => deselect([path]))
  }

  function unstagePath(path: string): Promise<void> {
    const row = rowOf(path)
    if (!allowed('unstage') || !row?.staged || !row.canUnstage) return Promise.resolve()
    return changeIndex(true, { paths: [path] }, () => deselect([path]))
  }

  function stageSelected(): Promise<void> {
    const paths = stageTargets(state.selected, state.status as WorktreeStatus | null)
    if (!allowed('stage') || !paths.length) return Promise.resolve()
    return changeIndex(false, { paths }, () => (state.selected = []))
  }

  function unstageSelected(): Promise<void> {
    const paths = unstageTargets(state.selected, state.status as WorktreeStatus | null)
    if (!allowed('unstage') || !paths.length) return Promise.resolve()
    return changeIndex(true, { paths }, () => (state.selected = []))
  }

  // The whole group, whatever the search, filter or folding shows (§3-3).
  function stageAll(): Promise<void> {
    if (!allowed('stage')) return Promise.resolve()
    return changeIndex(false, { all: true }, () => undefined)
  }

  // Also releases Stage on entries the list never shows (§3-6(1)); row flags do not apply.
  function unstageAll(): Promise<void> {
    if (!allowed('unstageAll')) return Promise.resolve()
    return changeIndex(true, { all: true }, () => undefined)
  }

  function changeFiles(remove: boolean, path: string, held?: WorktreeTicket | null): Promise<void> {
    if (held && !isCurrent(held)) return Promise.resolve()
    const row = rowOf(path)
    if (!allowed('stage') || !row || !(remove ? row.canRemove : row.canRevert)) return Promise.resolve()
    return write(
      'files',
      remove ? 'remove' : 'revert',
      (wt, gid) => (remove ? wt.remove(gid, [path], token()) : wt.revert(gid, [path], token())),
      (result, seq) => {
        applyAfter(result, seq)
        deselect([path])
        const changed = result.results.some((r) => r.result !== 'not_changed')
        if (!changed) setNotice('files', { code: 'path_not_changed' })
        else if (!result.status) setNotice('files', { code: 'status_reread' })
      },
      held,
    )
  }

  function revert(path: string, held?: WorktreeTicket | null): Promise<void> {
    return changeFiles(false, path, held)
  }

  function remove(path: string, held?: WorktreeTicket | null): Promise<void> {
    return changeFiles(true, path, held)
  }

  /** What the commit confirm dialog shows and fixes (count, index token, message, context). */
  function commitSnapshot(): WorktreeCommitSnapshot | null {
    const status = state.status
    const held = ticket()
    if (!status || !held || !allowed('commit')) return null
    return { count: status.counts.staged ?? 0, indexToken: status.indexToken, message: state.message, ticket: held }
  }

  // Only in the context the dialog was opened in: never another Flow's or project's index.
  function commit(snapshot: WorktreeCommitSnapshot): Promise<void> {
    if (!isCurrent(snapshot.ticket) || !allowed('commit')) return Promise.resolve()
    return write(
      'commit',
      'commit',
      (wt, gid) => wt.commit(gid, snapshot.message, snapshot.indexToken),
      (result, seq) => {
        state.message = ''
        state.suggestion = null
        state.selected = []
        applyAfter(result, seq)
        setNotice('commit', { code: 'committed', params: { commit: result.commit ?? '', n: result.committedCount } })
      },
      snapshot.ticket,
    )
  }

  // A draft only: the line is filled, nothing is committed (§3-3 AI 🤖).
  function suggest(held?: WorktreeTicket | null): Promise<void> {
    if (held && !isCurrent(held)) return Promise.resolve()
    const providerId = state.providerId
    if (!allowed('ai') || !providerId) return Promise.resolve()
    return write(
      'commit',
      'suggest',
      (wt, gid) => wt.suggestCommitMessage(gid, providerId),
      (draft) => {
        state.message = draft.suggested.slice(0, WORKTREE_MESSAGE_MAX)
        state.suggestion = { indexToken: draft.indexToken, inputTruncated: draft.inputTruncated }
      },
      held,
    )
  }

  function dispose(): void {
    disposed = true
    nextGeneration()
    scope.stop()
  }

  return {
    state: readonly(state) as DeepReadonly<WorktreeState>,
    gateOpen,
    sections,
    hiddenStaged,
    reasons,
    suggestionStale,
    changedCount,
    refresh,
    ticket,
    isCurrent,
    toggleFiles,
    toggleSearch,
    toggleMenu,
    toggleLock,
    setSearch,
    setFilter,
    setFolderMode,
    toggleFolder,
    showMore,
    setSelected,
    toggleSelectAll,
    clearSelection,
    stagePath,
    unstagePath,
    stageSelected,
    unstageSelected,
    stageAll,
    unstageAll,
    revert,
    remove,
    setMessage,
    commitSnapshot,
    commit,
    suggest,
    dismissNotice,
    dispose,
  }
}

// ── Panel context ────────────────────────────────────────────────────────────────────
// DesignerGitPanel provides this to the worktree area beside the 0033 GIT_PANEL_CONTEXT: the
// session, and the worktree diff intent only the panel can raise.

export interface GitWorktreeContext {
  worktree: GitWorktreeSession
  /** Raises a worktree `open-diff` intent for a listed row (compareBase null, §5-2). */
  openFileDiff(path: string, letter: string): void
  /**
   * Opens a confirm dialog bound to `ticket`: the panel closes it when that context goes (Flow /
   * project switch, gate closed, unmount) and runs nothing unless the ticket is still current.
   */
  confirm(request: GitConfirmRequest, ticket: WorktreeTicket): void
}

export const GIT_WORKTREE_CONTEXT: InjectionKey<GitWorktreeContext> = Symbol('designer-git-worktree')

export function useGitWorktree(): GitWorktreeContext {
  const context = inject(GIT_WORKTREE_CONTEXT, null)
  if (!context) throw new Error('Git worktree parts must be used inside DesignerGitPanel.')
  return context
}
