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
//
// T#7 adds the Base apply part (§3-2-2 ⑤–⑨, §3-3 merge–Fetch, §3-4): choosing a merge target
// voids the old judgment at once (⑤) and closes a target comparison Center Diff (⑧); the `Diff`
// pane list is tied to the target it was read for (⑦); a merge / follow-up push confirm fixes the
// judgment it showed and sends nothing once another one is on screen (⑥); E10 keeps its latest
// answer only (⑨). A merge, follow-up push or Fetch reads git/status and E1 / E8 / E10 again and
// never repeats itself; after a refused push the re-read judgment decides the hint (§3-4(4-1)).
import { computed, effectScope, inject, reactive, readonly, ref, watch, type ComputedRef, type DeepReadonly, type InjectionKey } from 'vue'
import type { FlowChangeEntry, ProjectGitStatus } from '@/features/project'
import { worktreeErrorOf } from './api'
import { createHttpGitSource, type GitSource, type GitWorktreeSource } from './gitSource'
import type { GitConfirmRequest, GitSession } from './gitSession'
import type {
  GitCenterDiffRefresh,
  ManualMergeResult,
  ManualMergeTarget,
  ManualMergeView,
  MergeResultRow,
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

/**
 * Where a notice line is drawn: under the changed-files header, under the commit line, in the
 * Base apply body (merge / Fetch) or in the follow-up push part.
 */
export type WorktreeNoticeArea = 'files' | 'commit' | 'apply' | 'push'

/** The inline pane of the Base apply body (`상태` / `Diff`); one at a time. */
export type WorktreeInfoPane = 'status' | 'diff'

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

// Merge / follow-up push / Fetch refusals with their own notice text (T#7, §3-4(4), §3-3).
export const APPLY_NOTICE_CODES: readonly string[] = [
  'graph_execution_active',
  'merge_conflict_open',
  'approval_retry_pending',
  'recovery_required',
  'invalid_state',
  'git_busy',
  'git_error',
  'git_unavailable',
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
  // ── Base apply (T#7) ──
  applyOpen: boolean
  // `머지 후 Push`: unchecked at first, kept while the Flow stays (§3-4(3)).
  pushAfterMerge: boolean
  infoPane: WorktreeInfoPane | null
  // The E8 / E10 numbers of the answers on screen: a confirm dialog fixes them (⑥).
  mergeSeq: number
  resultsSeq: number
  // The `Diff` pane list with the target it was read for (⑦); null while none is on screen.
  targetList: WorktreeTargetList | null
  targetListLoad: Load
  targetListLimit: number
  // The follow-up push row picked when there are two or more; null = the newest.
  pushChoice: number | null
  // The E10 row an `already_recorded` refusal points at.
  highlightMergeId: number | null
  // §3-4(4-1): after a refused merge + push the re-read judgment of the target decides the line.
  rejectHint: 'pending' | 'recovery' | 'rewound' | null
}

/** The `/changes?base=` list of the `Diff` pane and the target it was read for. */
export interface WorktreeTargetList {
  target: string
  changes: FlowChangeEntry[]
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

/**
 * What a merge confirm dialog showed and fixes (⑥): the target, the E8 answer it was made from,
 * its commit count and recovery flag, and whether it pushes.
 */
export interface WorktreeMergeSnapshot {
  target: string
  e8Seq: number
  ahead: number
  recovery: boolean
  push: boolean
  ticket: WorktreeTicket
}

/** What a follow-up push confirm dialog showed and fixes: the record, its commit, the E10 answer. */
export interface WorktreePushSnapshot {
  mergeId: number
  mergeCommit: string
  target: string
  isProjectBase: boolean
  e10Seq: number
  ticket: WorktreeTicket
}

/** The one-word state of the folded Base apply header (§3-4(1)); null = nothing to say. */
export interface WorktreeApplySummary {
  kind: 'merging' | 'conflict' | 'remote_pending' | 'checking' | 'ahead' | 'recovery' | 'nothing' | 'unknown'
  n?: number
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
  /** The folded Base apply header word (§3-4(1)). */
  readonly applySummary: ComputedRef<WorktreeApplySummary | null>
  /** E10 rows a follow-up push may send (newest first) and the one it would send now. */
  readonly pushCandidates: ComputedRef<DeepReadonly<MergeResultRow>[]>
  readonly pushRow: ComputedRef<DeepReadonly<MergeResultRow> | null>
  /** The 0033 session's git/status (Base remote state for the `상태` pane and the push dialog). */
  readonly projectGit: ComputedRef<DeepReadonly<ProjectGitStatus> | null>

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
  /** flowgate.v02.0067 T#2 — the folder checkbox: checks / unchecks these paths (selectable ones only). */
  selectPaths(paths: readonly string[], selected: boolean): void
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

  // ── Base apply (T#7) ──
  toggleApply(): void
  /** ⑤: the old judgment goes at once, the new target is read; ⑦ ⑧ follow. */
  setMergeTarget(target: string): void
  setPushAfterMerge(push: boolean): void
  toggleInfo(pane: WorktreeInfoPane): void
  showMoreTargetList(): void
  setPushChoice(mergeId: number): void
  mergeSnapshot(): WorktreeMergeSnapshot | null
  /** Sends nothing unless the context and the judgment the dialog showed are still on screen. */
  merge(snapshot: WorktreeMergeSnapshot): Promise<void>
  pushSnapshot(): WorktreePushSnapshot | null
  pushResult(snapshot: WorktreePushSnapshot): Promise<void>
  fetchRemote(): Promise<void>
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
    applyOpen: false,
    pushAfterMerge: false,
    infoPane: null,
    mergeSeq: 0,
    resultsSeq: 0,
    targetList: null,
    targetListLoad: 'idle',
    targetListLimit: WORKTREE_FOLDER_PAGE,
    pushChoice: null,
    highlightMergeId: null,
    rejectHint: null,
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
  // ⑦ The latest `Diff` pane list request.
  let listSeq = 0
  // §3-4(4-1): E8 answers asked after this number settle the hint of a refused merge push.
  let rejectFrom = 0
  // A merge target a person chose (the first default-target answer must not override it).
  let targetChosen = false
  let providersFor: string | null = null
  // ③ Reads asked for while a write runs, sent once afterwards (and coalesced otherwise).
  const pendingReads = { e1: false, e8: false, e10: false, list: false }
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

  // Rows a follow-up push may send, newest first; the picked one, else the newest (§3-4(5)).
  const pushCandidates = computed(() => (state.results?.results ?? []).filter((r) => r.pushAvailable))
  const pushRow = computed(
    () => pushCandidates.value.find((r) => r.mergeId === state.pushChoice) ?? pushCandidates.value[0] ?? null,
  )

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
      merge: {
        checking: state.mergeLoad === 'idle' || state.mergeLoad === 'loading',
        failed: state.mergeLoad === 'error',
        target: state.mergeTarget,
        view: state.merge as ManualMergeView | null,
      },
      push: { results: state.results as MergeResultsView | null, row: pushRow.value as MergeResultRow | null },
    }),
  )

  // §3-4(1), first rule that hits. Only the E8 judgment of the chosen target gives the number;
  // the display-only committed range never does.
  const applySummary = computed<WorktreeApplySummary | null>(() => {
    const groupState = state.status?.gitState.status ?? state.results?.groupStatus ?? null
    if (groupState === 'merging' || state.busy === 'merge') return { kind: 'merging' }
    if (groupState === 'conflict') return { kind: 'conflict' }
    const candidates = state.results?.pushCandidates ?? 0
    if (candidates > 0) return { kind: 'remote_pending', n: candidates }
    if (state.status && !state.status.worktreeAvailable) return null
    if (state.mergeLoad === 'loading') return { kind: 'checking' }
    if (state.mergeLoad === 'error') return { kind: 'unknown' }
    const merge = state.merge
    if (state.mergeLoad !== 'ready' || !merge || !state.mergeTarget || merge.aheadOfTarget === null) return null
    if (merge.aheadOfTarget > 0) return { kind: 'ahead', n: merge.aheadOfTarget }
    return { kind: merge.recovery ? 'recovery' : 'nothing' }
  })

  const projectGit = computed(() => git.state.status)

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
    pendingReads.e1 = pendingReads.e8 = pendingReads.e10 = pendingReads.list = false
    state.status = null
    state.statusLoad = 'idle'
    state.statusError = null
    state.merge = null
    state.mergeTargets = []
    state.mergeLoad = 'idle'
    state.mergeSeq = 0
    state.results = null
    state.resultsLoad = 'idle'
    state.resultsSeq = 0
    state.targetList = null
    state.targetListLoad = 'idle'
    state.pushChoice = null
    state.highlightMergeId = null
    state.rejectHint = null
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
    state.applyOpen = false
    state.pushAfterMerge = false
    state.infoPane = null
    state.targetListLimit = WORKTREE_FOLDER_PAGE
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

  // §3-2-3: the merge judgment of the chosen target is void until it is read again; an open `Diff`
  // pane reads the same target again (its list stays, "갱신 중"); an open Center Diff of this group
  // reads again. E10 does not depend on the workspace.
  function dependentsChanged(): void {
    const gid = groupId()
    state.merge = null
    if (state.mergeLoad !== 'idle') state.mergeLoad = 'loading'
    requestReads({ e8: true, list: state.infoPane === 'diff' })
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
      state.mergeTargets = view.targets
      state.mergeSeq = seq
      settleRejectHint(seq, view)
      const eligible = (name: string | null) => view.targets.some((t) => t.name === name && t.eligible)
      if (target === null && !targetChosen) {
        // The first answer picks the default target when it can be chosen.
        state.mergeTarget = eligible(view.defaultTarget) ? view.defaultTarget : null
      } else if (target === null || !eligible(target)) {
        // No target chosen, or the chosen one is gone / no longer eligible: no judgment, no_target.
        if (target !== null) dropTarget()
        state.merge = null
        state.mergeLoad = 'ready'
        return
      }
      state.merge = view
      state.mergeLoad = 'ready'
    } catch {
      if (myGen !== gen || disposed || seq !== e8Seq || target !== state.mergeTarget) return
      settleRejectHint(seq, null)
      state.merge = null
      state.mergeLoad = 'error'
    }
  }

  // The chosen target left the candidates: the selection empties and the pane list with it.
  function dropTarget(): void {
    state.mergeTarget = null
    state.targetList = null
    state.targetListLoad = 'idle'
  }

  // §3-4(4-1): only a judgment read after the refusal settles the line; a failed read says nothing.
  function settleRejectHint(seq: number, view: ManualMergeView | null): void {
    if (state.rejectHint !== 'pending' || seq <= rejectFrom) return
    if (view?.recovery) state.rejectHint = 'recovery'
    else if (view && (view.aheadOfTarget ?? 0) > 0) state.rejectHint = 'rewound'
    else state.rejectHint = null
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
      state.resultsSeq = seq
      state.resultsLoad = 'ready'
      if (state.pushChoice !== null && !results.results.some((r) => r.mergeId === state.pushChoice && r.pushAvailable)) {
        state.pushChoice = null
      }
    } catch {
      if (myGen !== gen || disposed || seq !== e10Seq) return
      state.resultsLoad = 'error'
    }
  }

  // ⑦ The `Diff` pane list of the chosen target; an answer applies only while that target is
  // still chosen and it is the latest request. A new target never shows the old list meanwhile.
  async function readTargetList(): Promise<void> {
    const wt = wsrc()
    const pid = projectId()
    const gid = groupId()
    if (!wt || !pid || !gid || disposed || state.infoPane !== 'diff') return
    const target = state.mergeTarget
    if (!target) {
      state.targetList = null
      state.targetListLoad = 'idle'
      return
    }
    const myGen = gen
    const seq = ++listSeq
    if (state.targetList && state.targetList.target !== target) state.targetList = null
    state.targetListLoad = 'loading'
    try {
      const set = await wt.loadTargetChanges(pid, gid, target)
      if (myGen !== gen || disposed || seq !== listSeq || target !== state.mergeTarget) return
      state.targetList = { target, changes: set.changes }
      state.targetListLoad = 'ready'
    } catch {
      if (myGen !== gen || disposed || seq !== listSeq || target !== state.mergeTarget) return
      state.targetList = null
      state.targetListLoad = 'error'
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
      pendingReads.e1 = pendingReads.e8 = pendingReads.e10 = pendingReads.list = false
      return
    }
    const reads = { ...pendingReads }
    pendingReads.e1 = pendingReads.e8 = pendingReads.e10 = pendingReads.list = false
    if (reads.e1) void readStatus()
    if (reads.e8) void readMerge()
    if (reads.e10) void readResults()
    if (reads.list) void readTargetList()
  }

  // Reads asked for in one turn go out once; during a write they wait for its answer (③).
  function requestReads(reads: { e1?: boolean; e8?: boolean; e10?: boolean; list?: boolean }): void {
    if (disposed) return
    pendingReads.e1 ||= !!reads.e1
    pendingReads.e8 ||= !!reads.e8
    pendingReads.e10 ||= !!reads.e10
    pendingReads.list ||= !!reads.list
    if (state.busy || flushQueued) return
    flushQueued = true
    void Promise.resolve().then(flushReads)
  }

  // An open `Diff` pane reads its list again with the rest.
  function refresh(): void {
    if (gateOpen.value) requestReads({ e1: true, e8: true, e10: true, list: state.infoPane === 'diff' })
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

  // The folder checkbox (0067-R §4-2): a selection only — what each path may do is still its row's.
  function selectPaths(paths: readonly string[], selected: boolean): void {
    const set = new Set(paths)
    if (!selected) {
      state.selected = state.selected.filter((p) => !set.has(p))
      return
    }
    const have = new Set(state.selected)
    const add = (state.status?.files ?? [])
      .filter((f) => set.has(f.path) && !have.has(f.path) && isSelectable(f as WorktreeFile))
      .map((f) => f.path)
    if (add.length) state.selected = [...state.selected, ...add]
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
  // `fail` replaces the common refusal handling (the merge / push / Fetch ones read more again).
  async function write<T>(
    area: WorktreeNoticeArea,
    key: string,
    send: (wt: GitWorktreeSource, gid: string) => Promise<T>,
    apply: (result: T, seq: number) => void,
    held?: WorktreeTicket | null,
    fail?: (error: unknown) => void,
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
      if (current()) (fail ?? ((e: unknown) => reportError(area, e)))(error)
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

  // ── Base apply (T#7, §3-4) ─────────────────────────────────────────────────────────

  // Folding closes the `상태` / `Diff` pane (§3-7 구역 열림).
  function toggleApply(): void {
    state.applyOpen = !state.applyOpen
    if (!state.applyOpen) state.infoPane = null
  }

  function setMergeTarget(target: string): void {
    if (!gateOpen.value || target === state.mergeTarget) return
    if (!state.mergeTargets.some((t) => t.name === target && t.eligible)) return
    targetChosen = true
    state.mergeTarget = target
    // ⑤ The old target's judgment leaves at once: "확인 중" until the new one is in.
    state.merge = null
    state.mergeLoad = 'loading'
    state.rejectHint = null
    // ⑦ An open `Diff` pane drops the old list now and reads the new target.
    if (state.infoPane === 'diff') {
      state.targetList = null
      state.targetListLoad = 'loading'
      state.targetListLimit = WORKTREE_FOLDER_PAGE
    }
    // ⑧ A Center Diff opened from the comparison list closes; a worktree file diff stays.
    const gid = groupId()
    if (gid) options.onCenterDiffRefresh?.({ groupId: gid, closeCompare: true })
    requestReads({ e8: true, list: state.infoPane === 'diff' })
  }

  function setPushAfterMerge(push: boolean): void {
    state.pushAfterMerge = push
  }

  // One pane at a time; opening `Diff` reads the chosen target's list.
  function toggleInfo(pane: WorktreeInfoPane): void {
    state.infoPane = state.infoPane === pane ? null : pane
    if (state.infoPane === 'diff') requestReads({ list: true })
  }

  function showMoreTargetList(): void {
    state.targetListLimit += WORKTREE_FOLDER_PAGE
  }

  function setPushChoice(mergeId: number): void {
    if (pushCandidates.value.some((r) => r.mergeId === mergeId)) state.pushChoice = mergeId
  }

  // A merge, push or Fetch moved Git outside the worktree: git/status (0033) and our reads again.
  function afterRemote(reads: { e1?: boolean; e8?: boolean; e10?: boolean }): void {
    void git.refresh()
    requestReads({ ...reads, list: !!reads.e8 && state.infoPane === 'diff' })
  }

  /** What the merge dialog shows and fixes (⑥); null when the merge cannot go now. */
  function mergeSnapshot(): WorktreeMergeSnapshot | null {
    const held = ticket()
    const view = state.merge
    const target = state.mergeTarget
    if (!held || !view || !target || state.mergeLoad !== 'ready' || !allowed('merge')) return null
    return {
      target,
      e8Seq: state.mergeSeq,
      ahead: view.aheadOfTarget ?? 0,
      recovery: view.recovery,
      push: state.pushAfterMerge,
      ticket: held,
    }
  }

  function mergedNotice(result: ManualMergeResult, target: string): WorktreeNotice {
    const params = { target: result.targetBranch ?? target }
    if (result.recovered) return { code: result.pushed ? 'recovered_pushed' : 'recovered', params }
    return { code: result.pushed ? 'merged_pushed' : 'merged', params }
  }

  // E9 with the target the dialog fixed. Merged: the area stays (worktree kept) and reads again;
  // conflict: the 0033 conflict card takes over through git/status.
  function merge(snapshot: WorktreeMergeSnapshot): Promise<void> {
    if (!isCurrent(snapshot.ticket)) return Promise.resolve()
    // ⑥ Another judgment on screen than the one the dialog showed: nothing is sent.
    if (state.mergeTarget !== snapshot.target || state.mergeLoad !== 'ready' || state.mergeSeq !== snapshot.e8Seq) {
      setNotice('apply', { code: 'judgment_changed', tone: 'error' })
      return Promise.resolve()
    }
    if (!allowed('merge')) return Promise.resolve()
    state.rejectHint = null
    state.highlightMergeId = null
    return write(
      'apply',
      'merge',
      (wt, gid) => wt.manualMerge(gid, snapshot.target, snapshot.push),
      (result) => {
        setNotice('apply', result.status === 'conflict' ? { code: 'merge_conflict' } : mergedNotice(result, snapshot.target))
        afterRemote({ e1: true, e8: true, e10: true })
      },
      snapshot.ticket,
      mergeFailed,
    )
  }

  // §3-4(4): one notice per refusal, what may have moved is read again, nothing is resent.
  function mergeFailed(error: unknown): void {
    const err = worktreeErrorOf(error)
    const code = err.code ?? ''
    const d = err.details
    if (err.network || err.httpStatus === 403 || code === 'forbidden') return reportError('apply', error)
    if (code === 'push_rejected') {
      // New merge rewound or an unpushed merge kept: only the re-read judgment can tell (4-1).
      setNotice('apply', { code: 'merge_push_rejected', tone: 'error' })
      state.rejectHint = 'pending'
      rejectFrom = e8Seq
      return afterRemote({ e1: true, e8: true, e10: true })
    }
    if (code === 'nothing_to_merge') {
      if (d.reason === 'already_recorded') {
        setNotice('apply', { code: 'already_recorded' })
        state.highlightMergeId = d.mergeId
        if (d.mergeId !== null) state.pushChoice = d.mergeId
      } else {
        setNotice('apply', { code: d.reason === 'target_synced_contains' ? 'target_synced_contains' : 'nothing_to_merge' })
      }
      return requestReads({ e8: true, e10: true })
    }
    if (code === 'uncommitted_changes') {
      const params = { n: d.count ?? 0, protected: d.protectedUncommitted ?? 0 }
      setNotice('apply', { code: params.protected > 0 ? 'uncommitted_protected' : 'uncommitted_changes', tone: 'error', params })
      return requestReads({ e1: true, e8: true })
    }
    if (code === 'git_busy' && d.jobId) {
      // Queued behind another Git job: the result shows on a refresh, never by sending again.
      setNotice('apply', { code: 'queued' })
      return requestReads({ e8: true, e10: true })
    }
    if (code.startsWith('merge_target_')) {
      setNotice('apply', { code: 'merge_target_unavailable', tone: 'error', params: { code } })
      return requestReads({ e8: true })
    }
    applyFailed('apply', err.code, err.httpStatus)
    if (err.httpStatus === 409) requestReads({ e1: true, e8: true })
  }

  // A refusal with its own apply text, else "failed ({code})".
  function applyFailed(area: WorktreeNoticeArea, code: string | null, httpStatus: number | null): void {
    const known = !!code && (APPLY_NOTICE_CODES.includes(code) || WORKTREE_NOTICE_CODES.includes(code))
    setNotice(
      area,
      known
        ? { code: code as string, tone: 'error' }
        : { code: 'failed', tone: 'error', params: { code: code || String(httpStatus ?? '') } },
    )
  }

  /** What the follow-up push dialog shows and fixes; null when no row can be pushed now. */
  function pushSnapshot(): WorktreePushSnapshot | null {
    const held = ticket()
    const row = pushRow.value
    if (!held || !row?.mergeCommit || !allowed('push')) return null
    return {
      mergeId: row.mergeId,
      mergeCommit: row.mergeCommit,
      target: row.target.targetBranch,
      isProjectBase: row.target.isProjectBase,
      e10Seq: state.resultsSeq,
      ticket: held,
    }
  }

  // E11 with the record the dialog fixed: never a branch name, never another row.
  function pushResult(snapshot: WorktreePushSnapshot): Promise<void> {
    if (!isCurrent(snapshot.ticket)) return Promise.resolve()
    const row = state.results?.results.find((r) => r.mergeId === snapshot.mergeId)
    if (state.resultsSeq !== snapshot.e10Seq || row?.mergeCommit !== snapshot.mergeCommit || !row.pushAvailable) {
      setNotice('push', { code: 'push_changed', tone: 'error' })
      return Promise.resolve()
    }
    if (!allowed('push')) return Promise.resolve()
    return write(
      'push',
      'push',
      (wt, gid) => wt.pushMergeResult(gid, snapshot.mergeId, snapshot.mergeCommit),
      (result) => {
        const params = { target: result.targetBranch ?? snapshot.target }
        setNotice('push', { code: result.alreadyPublished ? 'already_published' : 'pushed', params })
        state.highlightMergeId = null
        afterRemote({ e10: true })
      },
      snapshot.ticket,
      pushFailed,
    )
  }

  // §3-4(5): a moved record reads E10 again and is never swapped for another row.
  function pushFailed(error: unknown): void {
    const err = worktreeErrorOf(error)
    const code = err.code ?? ''
    if (err.network || err.httpStatus === 403 || code === 'forbidden') return reportError('push', error)
    if (code === 'stale_target' || code === 'merge_target_moved' || code === 'merge_result_missing') {
      setNotice('push', { code: 'record_changed', tone: 'error' })
    } else if (code === 'push_rejected') {
      // E11 never rewinds the target: the merge result stays.
      setNotice('push', { code: 'push_rejected_kept', tone: 'error' })
    } else if (code === 'git_busy' && err.details.jobId) {
      setNotice('push', { code: 'queued' })
    } else {
      applyFailed('push', err.code, err.httpStatus)
    }
    requestReads({ e10: true })
  }

  // E12 only: remote-tracking refs; no Pull, no merge, no working file changes (§3-4(6)).
  function fetchRemote(): Promise<void> {
    const pid = projectId()
    if (!pid || !allowed('fetch')) return Promise.resolve()
    return write(
      'apply',
      'fetch',
      (wt, gid) => wt.fetchRefs(pid, gid),
      () => {
        setNotice('apply', { code: 'fetched' })
        afterRemote({ e8: true, e10: true })
      },
      null,
      (error) => {
        const err = worktreeErrorOf(error)
        if (err.network || err.httpStatus === 403 || err.code === 'forbidden') return reportError('apply', error)
        applyFailed('apply', err.code, err.httpStatus)
      },
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
    applySummary,
    pushCandidates,
    pushRow,
    projectGit,
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
    selectPaths,
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
    toggleApply,
    setMergeTarget,
    setPushAfterMerge,
    toggleInfo,
    showMoreTargetList,
    setPushChoice,
    mergeSnapshot,
    merge,
    pushSnapshot,
    pushResult,
    fetchRemote,
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
  /** Raises the `open-diff` intent of a `Diff` pane row: compareBase = the target its list was read for. */
  openTargetDiff(path: string, letter: string, target: string): void
  /**
   * Opens a confirm dialog bound to `ticket`: the panel closes it when that context goes (Flow /
   * project switch, gate closed, unmount) or `stillValid` turns false (e.g. the merge target
   * changed, ⑤), and runs nothing unless the ticket is still current.
   */
  confirm(request: GitConfirmRequest, ticket: WorktreeTicket, stillValid?: () => boolean): void
}

export const GIT_WORKTREE_CONTEXT: InjectionKey<GitWorktreeContext> = Symbol('designer-git-worktree')

export function useGitWorktree(): GitWorktreeContext {
  const context = inject(GIT_WORKTREE_CONTEXT, null)
  if (!context) throw new Error('Git worktree parts must be used inside DesignerGitPanel.')
  return context
}
