// flowgate.v02.0033 — Git session: the single owner of the Designer Git part's state
// (0033-D §4.2). Not a Pinia store: DesignerGitPanel creates one and it lives and dies with the
// panel, like the Conversation / Designer Center sessions. Cards read `state` and the derived
// views and change them only through the actions; every server call goes through GitSource.
//
// What is server state stays server state: the session keeps the last git/status answer, the
// active group's conflict / review / AI-run answers, and derives cards and the badge from them.
// It keeps on its own only what the server has no record of: the approve attempt id, the
// reject reason and commit message being typed, the remote-apply failure record (G1) and the
// modeless windows.
//
// Two stages (0033-D §3.4, §3.5, §8): S1 / S2 fields missing from git/status = stage 1, the
// interim behaviour on the existing contract; present = stage 2. The choice is made per answer,
// from the fields alone — there is no version negotiation.
import { computed, inject, reactive, readonly, type ComputedRef, type DeepReadonly, type InjectionKey } from 'vue'
import { randomUuid } from '@shared/utils/uuid'
import type { FlowAiActivity, FlowRef, ProjectGitPendingRow, ProjectGitStatus } from '@/features/project'
import { gitErrorOf } from './api'
import { createHttpGitSource, type GitSource } from './gitSource'
import type {
  GitApplyRequest,
  GitBadge,
  GitCardKey,
  GitConflictSession,
  GitFileLetter,
  GitFileOrigin,
  GitFinalizeAction,
  GitNotice,
  GitProviders,
  GitPushFailure,
  GitReview,
  GitWindowDescriptor,
} from './types'

// Same short polling cadence as the Conversation part's AI-run polling (0033-D §4.3).
export const GIT_POLL_MS = 2500
// Files a card lists before "외 N개" (v7 mock-up: three).
export const FILE_PREVIEW = 3
export const REJECT_REASON_MAX = 4000

/** General merge review states (0033-D §3.7). Anything else on a conflict row is not a review. */
export const REVIEW_STATES: readonly string[] = ['resolved_pending_review', 're_review', 'applying', 'reconciling']
const POLLED_REVIEW_STATES: readonly string[] = ['applying', 'reconciling']
const APPROVE_TERMINAL: readonly string[] = ['completed', 'merged', 'pushed', 'already_applied']
const APPROVE_APPLY_FAILED: readonly string[] = ['pre_commit_validation_failed', 'commit_creation_failed']
export const FINALIZE_ACTIONS: readonly GitFinalizeAction[] = ['merge', 'merge_only', 'push', 'commit_push']

// Codes that have their own notice text; anything else reads as the generic failure + code.
const NOTICE_CODES: readonly string[] = [
  'git_busy',
  'invalid_state',
  'invalid_request',
  'stale_review',
  'review_not_ready',
  'restoration_verification_failed',
  'invalid_reason',
  'base_dirty',
  'base_untracked_conflict',
  'final_approval_bound',
]

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

/** The group-scoped card of the active Flow, at most one (0033-D §3.2–§3.4, §3.7). */
export type GitGroupCard = 'conflict' | 'ai' | 'review' | 'unsupported' | 'hold' | null

export interface GitHoldView {
  groupId: string
  acDocId: string | null
  stage: 1 | 2
  // The branch the apply lands on; null = the project Base is not configured.
  targetBranch: string | null
  // S1 planned action; null in stage 1 or when the group has no plan.
  action: GitFinalizeAction | null
  // Sent as git_target_branch (S1 planned target of a merge / merge_only only).
  requestTargetBranch: string | null
  blockReason: 'stage1' | 'no_plan' | 'final_approval_bound' | null
}

export interface GitDirtyView {
  stage: 1 | 2
  tracked: string[]
  // Received new-file paths; only a part of them when `truncated`.
  untracked: string[]
  truncated: boolean
  // S2 total of new files (stage 2 only).
  untrackedTotal: number | null
  // Exact number of changed files, or null when only a lower bound is known (stage 1, truncated).
  count: number | null
  // tracked + received new files: the badge's lower bound when `count` is null.
  lowerBound: number
  // New files the list does not show (stage 2, truncated).
  hiddenUntracked: number
  blockReason: 'ai_run' | 'truncated' | null
}

export interface GitState {
  projectId: string | null
  flow: FlowRef | null
  readOnly: boolean
  statusLoad: LoadStatus
  // Why the last git/status read failed: 403/404 and the like read as "unreadable".
  statusError: 'unreadable' | 'network' | null
  status: ProjectGitStatus | null
  // Bumped per applied git/status answer; tracked-file letters are kept per generation.
  statusGen: number
  ai: FlowAiActivity | null
  conflict: GitConflictSession | null
  conflictLoad: LoadStatus
  review: GitReview | null
  reviewLoad: LoadStatus
  providers: GitProviders | null
  // Tracked uncommitted file letter by path for this status generation; null = not known.
  fileStatus: Record<string, GitFileLetter | null>
  // The one action in flight (every action button is disabled meanwhile).
  busy: string | null
  // A 403 on an action: every action stays disabled (0033-D §4.5).
  forbidden: boolean
  notices: Partial<Record<GitCardKey, GitNotice>>
  commitMessage: string
  rejectOpen: boolean
  rejectReason: string
  pushFailure: GitPushFailure | null
  windows: GitWindowDescriptor[]
}

export interface GitSession {
  readonly state: DeepReadonly<GitState>
  readonly pendingRow: ComputedRef<DeepReadonly<ProjectGitPendingRow> | null>
  readonly groupCard: ComputedRef<GitGroupCard>
  readonly holdView: ComputedRef<GitHoldView | null>
  readonly dirtyView: ComputedRef<GitDirtyView | null>
  readonly badge: ComputedRef<GitBadge | null>
  readonly workBranch: ComputedRef<string | null>
  readonly defaultProviderId: ComputedRef<string | null>
  /** False while read-only, forbidden or another action runs. */
  readonly canAct: ComputedRef<boolean>
  /** The conflicts answer for the active merge is in and says an ordinary merge (§3.2, §3.7). */
  readonly conflictActionable: ComputedRef<boolean>

  setContext(projectId: string | null, flow: FlowRef | null): void
  setReadOnly(readOnly: boolean): void
  refresh(): Promise<void>

  startAi(card?: GitCardKey): Promise<void>
  abortMerge(): Promise<void>
  cancelAi(): Promise<void>
  approve(): Promise<void>
  openReject(open: boolean): void
  setRejectReason(reason: string): void
  reject(): Promise<void>
  applyNow(): Promise<void>
  retryApply(): Promise<void>
  setCommitMessage(message: string): void
  commit(): Promise<void>
  revertAll(): Promise<void>
  /** Asks the server for the A/D/M letter of tracked rows actually drawn (0033-D §3.5). */
  ensureFileStatus(paths: readonly string[]): void
  loadConflicts(): Promise<void>

  openConflictWindow(title: string): void
  openPushErrorWindow(title: string, groupId?: string): void
  restoreWindow(id: string): void
  closeWindow(id: string): void
  setWindowMinimized(id: string, minimized: boolean): void

  dismissNotice(card: GitCardKey): void
  dispose(): void
}

export interface GitSessionOptions {
  source?: GitSource
  /** Wait between polls; tests replace it. */
  sleep?: (ms: number) => Promise<void>
  /** Clock for the failure record; tests replace it. */
  now?: () => Date
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

const isResolving = (ai: FlowAiActivity | null): boolean => !!ai?.active && ai.actionScope === 'resolve_conflict'

export function conflictWindowId(groupId: string, mergeId: number): string {
  return `conflict:${groupId}:${mergeId}`
}
export const PUSH_ERROR_WINDOW_ID = 'push-error'

export function createGitSession(options: GitSessionOptions = {}): GitSession {
  // The HTTP source needs the Pinia app the Designer runs in; only built when none is given.
  let source = options.source ?? null
  const src = (): GitSource => (source ??= createHttpGitSource())
  const sleep = options.sleep ?? defaultSleep
  const now = options.now ?? (() => new Date())

  const state = reactive<GitState>({
    projectId: null,
    flow: null,
    readOnly: false,
    statusLoad: 'idle',
    statusError: null,
    status: null,
    statusGen: 0,
    ai: null,
    conflict: null,
    conflictLoad: 'idle',
    review: null,
    reviewLoad: 'idle',
    providers: null,
    fileStatus: {},
    busy: null,
    forbidden: false,
    notices: {},
    commitMessage: '',
    rejectOpen: false,
    rejectReason: '',
    pushFailure: null,
    windows: [],
  })

  let disposed = false
  // Bumped on every project / Flow switch; an answer for an older context is dropped (§4.1).
  let ctx = 0
  // Numbers git/status reads; only the latest started one may apply.
  let statusSeq = 0
  let polling = false
  let providersFor: string | null = null
  // Approve attempt (§4.4): the same id is resent after a network failure, a new one follows
  // any non-terminal answer or a changed candidate.
  let attempt: { mergeId: number; fingerprint: string; id: string } | null = null

  // ── Derived views ──────────────────────────────────────────────────────────────────

  const pendingRow = computed(() => {
    const gid = state.flow?.groupId
    if (!gid || !state.status?.enabled) return null
    return state.status.pending.find((row) => row.groupId === gid) ?? null
  })

  const groupCard = computed<GitGroupCard>(() => {
    const row = pendingRow.value
    if (!row || state.statusLoad === 'error') return null
    if (row.status === 'waiting') return 'hold'
    if (row.status !== 'conflict' || row.mergeId === null) return null
    // A running resolver wins over any review state (§3.3).
    if (isResolving(state.ai)) return 'ai'
    if (row.reviewState && REVIEW_STATES.includes(row.reviewState)) return 'review'
    // TR revert conflict (open / resolved) or a state this part does not know (§3.7).
    if (row.reviewState) return 'unsupported'
    // git/status sends review_state = null for every non-merge session, a TR revert / reapply
    // included; the session kind the conflicts answer carries tells them apart.
    if (state.conflict?.mergeId === row.mergeId && state.conflict.trConflict) return 'unsupported'
    return 'conflict'
  })

  // Until the conflicts answer for this merge says what kind of session it is, the conflict
  // card offers no action: it could still turn out to be a TR conflict (§3.7).
  const conflictActionable = computed(() => {
    const row = pendingRow.value
    if (!row || row.status !== 'conflict' || row.mergeId === null) return false
    const conflict = state.conflict
    return conflict?.mergeId === row.mergeId && !conflict.trConflict
  })

  const holdView = computed<GitHoldView | null>(() => {
    const row = pendingRow.value
    if (groupCard.value !== 'hold' || !row) return null
    const base = state.status?.baseBranch ?? null
    const plan = row.plan
    if (!plan) {
      return {
        groupId: row.groupId,
        acDocId: row.acDocId,
        stage: 1,
        targetBranch: base,
        action: null,
        requestTargetBranch: null,
        blockReason: 'stage1',
      }
    }
    const action = FINALIZE_ACTIONS.find((a) => a === plan.action) ?? null
    const mergeLike = action === 'merge' || action === 'merge_only'
    return {
      groupId: row.groupId,
      acDocId: row.acDocId,
      stage: 2,
      targetBranch: plan.targetBranch ?? base,
      action,
      requestTargetBranch: mergeLike ? plan.targetBranch : null,
      blockReason: !action ? 'no_plan' : row.finalApprovalBound ? 'final_approval_bound' : null,
    }
  })

  const s2Actions = (): boolean => !!src().baseCommitAll && !!src().baseRevertAll

  const dirtyView = computed<GitDirtyView | null>(() => {
    const status = state.status
    if (!status?.enabled || state.statusLoad === 'error') return null
    // A stopped merge owns the Base changes: that is the conflict session, not stray edits.
    if (status.baseDirty.mergeInProgress) return null
    const tracked = status.baseDirty.files
    const untracked = status.baseUntracked.files
    const total = status.baseUntracked.total
    if (!tracked.length && !untracked.length && !total) return null
    const stage = total === null ? 1 : 2
    const truncated = status.baseUntracked.truncated
    const lowerBound = tracked.length + untracked.length
    let count: number | null = lowerBound
    if (truncated) count = stage === 2 ? tracked.length + (total as number) : null
    return {
      stage,
      tracked: [...tracked],
      untracked: [...untracked],
      truncated,
      untrackedTotal: total,
      count,
      lowerBound,
      hiddenUntracked: stage === 2 && truncated ? Math.max(0, (total as number) - untracked.length) : 0,
      blockReason: status.baseDirty.aiRun ? 'ai_run' : truncated && !(stage === 2 && s2Actions()) ? 'truncated' : null,
    }
  })

  const badge = computed<GitBadge | null>(() => {
    const card = groupCard.value
    if (card === 'conflict' || card === 'review' || card === 'unsupported' || state.pushFailure) {
      return { tone: 'danger', text: '!' }
    }
    if (card === 'ai') return { tone: 'progress', text: '…' }
    const dirty = dirtyView.value
    const held = card === 'hold' ? 1 : 0
    if (!dirty && !held) return null
    if (dirty && dirty.count === null) return { tone: 'count', text: `${held + dirty.lowerBound}+` }
    return { tone: 'count', text: String(held + (dirty?.count ?? 0)) }
  })

  const workBranch = computed(() => {
    const flow = state.flow
    if (!flow) return null
    if (flow.branch) return flow.branch
    return state.status?.slots.find((slot) => slot.groupId === flow.groupId)?.branch ?? null
  })

  const defaultProviderId = computed(() => {
    const providers = state.providers
    if (!providers?.defaultProviderId) return null
    return providers.providerIds.includes(providers.defaultProviderId) ? providers.defaultProviderId : null
  })

  const canAct = computed(() => !state.readOnly && !state.forbidden && !state.busy)

  // ── Notices ────────────────────────────────────────────────────────────────────────

  function setNotice(card: GitCardKey, notice: GitNotice): void {
    state.notices = { ...state.notices, [card]: notice }
  }

  function dismissNotice(card: GitCardKey): void {
    if (!(card in state.notices)) return
    const next = { ...state.notices }
    delete next[card]
    state.notices = next
  }

  // A notice belongs to its card; once that card is gone the line goes with it (§4.5).
  function pruneNotices(): void {
    const card = groupCard.value
    const alive: Record<GitCardKey, boolean> = {
      header: true,
      conflict: card === 'conflict' || card === 'ai' || card === 'unsupported',
      review: card === 'review',
      hold: card === 'hold',
      dirty: !!dirtyView.value,
      push: !!state.pushFailure,
      window: state.windows.length > 0,
    }
    for (const key of Object.keys(state.notices) as GitCardKey[]) if (!alive[key]) dismissNotice(key)
  }

  function reportError(card: GitCardKey, error: unknown): void {
    const err = gitErrorOf(error)
    if (err.network) return setNotice(card, { code: 'network' })
    if (err.status === 403 || err.code === 'forbidden') {
      state.forbidden = true
      return setNotice(card, { code: 'forbidden' })
    }
    if (err.code && NOTICE_CODES.includes(err.code)) return setNotice(card, { code: err.code })
    if (err.code?.startsWith('merge_target_')) return setNotice(card, { code: 'merge_target' })
    setNotice(card, { code: 'failed', params: { code: err.code ?? String(err.status ?? '') } })
  }

  // ── Context ────────────────────────────────────────────────────────────────────────

  function resetGroup(): void {
    state.ai = null
    state.conflict = null
    state.conflictLoad = 'idle'
    state.review = null
    state.reviewLoad = 'idle'
    state.rejectOpen = false
    state.rejectReason = ''
    attempt = null
    const gid = state.flow?.groupId ?? null
    // Another group's conflict window is closed; project-level windows stay (§4.1).
    state.windows = state.windows.filter((w) => w.kind !== 'conflict' || w.groupId === gid)
  }

  function resetProject(): void {
    state.status = null
    state.statusLoad = 'idle'
    state.statusError = null
    state.fileStatus = {}
    state.providers = null
    providersFor = null
    state.forbidden = false
    state.commitMessage = ''
    state.pushFailure = null
    state.windows = []
    state.notices = {}
    resetGroup()
  }

  function setContext(projectId: string | null, flow: FlowRef | null): void {
    const projectChanged = projectId !== state.projectId
    const groupChanged = (flow?.groupId ?? null) !== (state.flow?.groupId ?? null)
    state.flow = flow ? { groupId: flow.groupId, name: flow.name, branch: flow.branch ?? null } : null
    if (!projectChanged && !groupChanged) return
    ctx += 1
    if (projectChanged) {
      state.projectId = projectId
      resetProject()
    } else {
      resetGroup()
      for (const key of ['conflict', 'review', 'hold', 'window'] as GitCardKey[]) dismissNotice(key)
    }
    void load({ forceConflicts: true })
  }

  function setReadOnly(readOnly: boolean): void {
    state.readOnly = readOnly
  }

  // ── Reads ──────────────────────────────────────────────────────────────────────────

  async function loadConflictsFor(myCtx: number, groupId: string, mergeId: number): Promise<void> {
    state.conflictLoad = 'loading'
    try {
      const conflict = await src().fetchConflicts(groupId, mergeId)
      if (myCtx !== ctx || disposed) return
      state.conflict = conflict
      state.conflictLoad = 'ready'
    } catch (error) {
      if (myCtx !== ctx || disposed) return
      state.conflictLoad = 'error'
      reportError(state.windows.some((w) => w.kind === 'conflict') ? 'window' : 'conflict', error)
    }
  }

  async function loadReviewFor(myCtx: number, groupId: string, mergeId: number): Promise<void> {
    state.reviewLoad = 'loading'
    try {
      const review = await src().fetchReview(groupId, mergeId)
      if (myCtx !== ctx || disposed) return
      state.review = review
      state.reviewLoad = 'ready'
    } catch (error) {
      if (myCtx !== ctx || disposed) return
      state.reviewLoad = 'error'
      reportError('review', error)
    }
  }

  async function loadProviders(myCtx: number, projectId: string): Promise<void> {
    if (providersFor === projectId) return
    providersFor = projectId
    try {
      const providers = await src().fetchProviders(projectId)
      if (myCtx !== ctx || disposed) return
      state.providers = providers
    } catch {
      // No list = no default AI: reject stays disabled with its reason (§3.7). Asked again
      // the next time the review card is read.
      if (myCtx === ctx) providersFor = null
    }
  }

  // Group part of a refresh: AI run, conflict files, review — for the active group only.
  async function syncGroup(myCtx: number, forceConflicts: boolean): Promise<void> {
    const row = pendingRow.value
    const gid = state.flow?.groupId
    if (!row || !gid || row.status !== 'conflict' || row.mergeId === null) {
      state.ai = null
      state.conflict = null
      state.conflictLoad = 'idle'
      state.review = null
      state.reviewLoad = 'idle'
      state.rejectOpen = false
      attempt = null
      // A window for a conflict that is no longer there has nothing left to show.
      state.windows = state.windows.filter((w) => w.kind !== 'conflict')
      return
    }
    const mergeId = row.mergeId
    state.windows = state.windows.filter((w) => w.kind !== 'conflict' || w.mergeId === mergeId)
    const wasResolving = isResolving(state.ai)
    try {
      const ai = await src().loadAiActivity(gid)
      if (myCtx !== ctx || disposed) return
      state.ai = ai
    } catch {
      // Keep the last answer; the next refresh asks again.
    }
    const resolving = isResolving(state.ai)
    const aiEnded = wasResolving && !resolving
    const reads: Promise<void>[] = []
    if (forceConflicts || aiEnded || state.conflict?.mergeId !== mergeId) reads.push(loadConflictsFor(myCtx, gid, mergeId))
    if (!resolving && row.reviewState && REVIEW_STATES.includes(row.reviewState)) {
      reads.push(loadReviewFor(myCtx, gid, mergeId))
      if (state.projectId) reads.push(loadProviders(myCtx, state.projectId))
    } else if (!row.reviewState || resolving) {
      state.review = null
      state.reviewLoad = 'idle'
      state.rejectOpen = false
    }
    await Promise.all(reads)
    if (myCtx !== ctx || disposed) return
    // A TR conflict has no window here (§3.7).
    if (state.conflict?.mergeId === mergeId && state.conflict.trConflict) {
      state.windows = state.windows.filter((w) => w.kind !== 'conflict')
    }
  }

  async function load(opts: { forceConflicts?: boolean } = {}): Promise<void> {
    const pid = state.projectId
    if (!pid || disposed) return
    const myCtx = ctx
    const seq = ++statusSeq
    if (state.statusLoad !== 'ready') state.statusLoad = 'loading'
    let status: ProjectGitStatus | null
    try {
      status = await src().loadStatus(pid)
    } catch (error) {
      if (myCtx !== ctx || seq !== statusSeq || disposed) return
      const err = gitErrorOf(error)
      if (err.network && state.status) {
        // Keep showing the last answer; the next refresh point reads again (§4.5).
        setNotice('header', { code: 'network' })
        return
      }
      state.status = null
      state.statusLoad = 'error'
      state.statusError = err.network ? 'network' : 'unreadable'
      pruneNotices()
      return
    }
    if (myCtx !== ctx || seq !== statusSeq || disposed) return
    state.status = status
    state.statusLoad = 'ready'
    state.statusError = null
    state.statusGen += 1
    state.fileStatus = {}
    dismissNotice('header')
    await syncGroup(myCtx, opts.forceConflicts === true)
    if (myCtx !== ctx || disposed) return
    pruneNotices()
    schedulePoll()
  }

  function shouldPoll(): boolean {
    const card = groupCard.value
    if (card === 'ai') return true
    if (card !== 'review') return false
    const reviewState = state.review?.reviewState ?? pendingRow.value?.reviewState ?? null
    return !!reviewState && POLLED_REVIEW_STATES.includes(reviewState)
  }

  // AI resolving and review applying / reconciling are the only polled states (§4.3).
  function schedulePoll(): void {
    if (polling || disposed || !shouldPoll()) return
    polling = true
    void (async () => {
      try {
        // No iteration cap: the poll runs for as long as the polled state persists (§4.3).
        while (!disposed && shouldPoll()) {
          await sleep(GIT_POLL_MS)
          if (disposed || !shouldPoll()) return
          await load()
        }
      } finally {
        polling = false
      }
    })()
  }

  function refresh(): Promise<void> {
    return load({ forceConflicts: true })
  }

  function loadConflicts(): Promise<void> {
    const row = pendingRow.value
    if (!row || row.mergeId === null || row.status !== 'conflict') return Promise.resolve()
    return loadConflictsFor(ctx, row.groupId, row.mergeId)
  }

  function ensureFileStatus(paths: readonly string[]): void {
    const pid = state.projectId
    if (!pid) return
    const gen = state.statusGen
    const asked: Record<string, GitFileLetter | null> = {}
    for (const path of paths) if (!(path in state.fileStatus)) asked[path] = null
    const todo = Object.keys(asked)
    if (!todo.length) return
    state.fileStatus = { ...state.fileStatus, ...asked }
    for (const path of todo) {
      src()
        .loadBaseFileDiff(pid, path)
        .then((diff) => {
          if (disposed || gen !== state.statusGen || pid !== state.projectId) return
          state.fileStatus = { ...state.fileStatus, [path]: diff.status }
        })
        // 404 (hidden / vanished path) or any failure: the row stays "status unknown" and
        // the card shows no error line (§4.4).
        .catch(() => undefined)
    }
  }

  // ── Actions ────────────────────────────────────────────────────────────────────────

  // One action at a time; whatever happens, git/status is read again afterwards (§4.4).
  async function act(card: GitCardKey, key: string, run: () => Promise<void>, after: { forceConflicts?: boolean } = {}): Promise<void> {
    if (!canAct.value || disposed) return
    const myCtx = ctx
    state.busy = key
    dismissNotice(card)
    try {
      await run()
    } catch (error) {
      if (myCtx === ctx) reportError(card, error)
    } finally {
      state.busy = null
    }
    if (myCtx !== ctx || disposed) return
    await load(after)
  }

  function activeConflict(): { groupId: string; mergeId: number; row: DeepReadonly<ProjectGitPendingRow> } | null {
    const row = pendingRow.value
    if (!row || row.status !== 'conflict' || row.mergeId === null) return null
    return { groupId: row.groupId, mergeId: row.mergeId, row }
  }

  function startAi(card: GitCardKey = 'conflict'): Promise<void> {
    const target = activeConflict()
    if (!target || isResolving(state.ai) || !conflictActionable.value) return Promise.resolve()
    return act(card, 'ai', async () => {
      try {
        const runId = await src().startConflictAi(target.groupId, target.mergeId)
        // Optimistic only until the first active read right after (§4.4 job/session).
        state.ai = { active: true, runId, status: 'running', actionScope: 'resolve_conflict' }
      } catch (error) {
        const err = gitErrorOf(error)
        if (!err.network && err.status === 409) {
          setNotice(card, { code: 'ai_running' })
          return
        }
        throw error
      }
    })
  }

  function abortMerge(): Promise<void> {
    const target = activeConflict()
    if (!target) return Promise.resolve()
    // The review card's 취소 needs no conflicts answer; the conflict card's does (§3.7).
    if (groupCard.value !== 'review' && !conflictActionable.value) return Promise.resolve()
    const card: GitCardKey = groupCard.value === 'review' ? 'review' : 'conflict'
    return act(card, 'abort', () => src().abortMerge(target.groupId, target.mergeId))
  }

  function cancelAi(): Promise<void> {
    const runId = state.ai?.runId
    if (!runId) return load()
    return act('conflict', 'cancel', async () => {
      try {
        await src().cancelAiRun(runId)
      } catch (error) {
        const err = gitErrorOf(error)
        // No such run / already over: the re-read shows where things stand.
        if (!err.network && (err.status === 404 || err.status === 409)) return
        throw error
      }
    }, { forceConflicts: true })
  }

  function approve(): Promise<void> {
    const target = activeConflict()
    const review = state.review
    if (!target || !review?.fingerprint || !review.canApprove || review.mergeId !== target.mergeId) return Promise.resolve()
    const fingerprint = review.fingerprint
    if (!attempt || attempt.mergeId !== target.mergeId || attempt.fingerprint !== fingerprint) {
      attempt = { mergeId: target.mergeId, fingerprint, id: randomUuid() }
    }
    const attemptId = attempt.id
    return act('review', 'approve', async () => {
      let result
      try {
        result = await src().approveReview(target.groupId, target.mergeId, attemptId, fingerprint)
      } catch (error) {
        const err = gitErrorOf(error)
        // A resend after a network failure must carry the same attempt id.
        if (err.network) throw error
        attempt = null
        if (err.code === 'review_not_ready' || err.code === 'invalid_state') return
        if (err.code === 'invalid_attempt_id') {
          setNotice('review', { code: 'failed', params: { code: err.code } })
          return
        }
        throw error
      }
      // Any answer that is not final gets a new attempt id next time.
      attempt = null
      if (APPROVE_TERMINAL.includes(result.status)) return
      if (APPROVE_APPLY_FAILED.includes(result.status)) {
        setNotice('review', { code: 'apply_failed', params: { n: result.errorCount } })
      }
      // re_review / reconciling: the review re-read carries the new state, its reason line
      // and (re_review) the new fingerprint. A re_review after a rejected push is NOT a
      // remote-apply failure record (§3.6).
    })
  }

  function openReject(open: boolean): void {
    state.rejectOpen = open
    if (!open) state.rejectReason = ''
    dismissNotice('review')
  }

  function setRejectReason(reason: string): void {
    state.rejectReason = reason
  }

  function reject(): Promise<void> {
    const target = activeConflict()
    const review = state.review
    const providerId = defaultProviderId.value
    if (!target || !review?.canReject || !providerId) return Promise.resolve()
    const reason = state.rejectReason.trim()
    if (!reason || reason.length > REJECT_REASON_MAX) {
      setNotice('review', { code: 'invalid_reason' })
      return Promise.resolve()
    }
    return act('review', 'reject', async () => {
      const result = await src().rejectReview(target.groupId, target.mergeId, reason, providerId)
      state.rejectOpen = false
      state.rejectReason = ''
      // The server restored the conflict and started a new resolver run on the group.
      if (result.resolverRunId) {
        state.ai = { active: true, runId: result.resolverRunId, status: 'running', actionScope: 'resolve_conflict' }
      }
    }, { forceConflicts: true })
  }

  function targetOf(request: GitApplyRequest): string {
    if (request.kind === 'finalize') return request.targetBranch ?? state.status?.baseBranch ?? request.groupId
    return request.branch ?? state.status?.baseBranch ?? ''
  }

  // Runs a remote-apply request; a push_rejected answer becomes (or refreshes) the failure
  // record holding the very request, so `다시 반영` resends it unchanged (§3.6, §4.4).
  async function runApply(request: GitApplyRequest): Promise<void> {
    try {
      if (request.kind === 'finalize') await src().finalize(request.groupId, request.action, request.targetBranch)
      else await src().push(request.projectId, request.branch)
      if (state.pushFailure && state.pushFailure.request === request) state.pushFailure = null
    } catch (error) {
      const err = gitErrorOf(error)
      if (!err.network && err.code === 'push_rejected') {
        state.pushFailure = {
          request,
          code: err.code,
          httpStatus: err.status,
          at: now().toISOString(),
          target: targetOf(request),
        }
        return
      }
      throw error
    }
  }

  function applyNow(): Promise<void> {
    const hold = holdView.value
    if (!hold || hold.blockReason || !hold.action) return Promise.resolve()
    const request: GitApplyRequest = {
      kind: 'finalize',
      groupId: hold.groupId,
      action: hold.action,
      targetBranch: hold.requestTargetBranch,
    }
    return act('hold', 'apply', () => runApply(request))
  }

  function retryApply(): Promise<void> {
    const failure = state.pushFailure
    if (!failure) return Promise.resolve()
    // The stored request object itself, never a request rebuilt from today's state.
    const request = failure.request as GitApplyRequest
    return act('push', 'retry', () => runApply(request))
  }

  function setCommitMessage(message: string): void {
    state.commitMessage = message
  }

  // A 409 from an S2 "all new files" request that is not one of the ordinary refusals means
  // the new-file total changed since it was shown: nothing was changed, show it again (§3.5).
  function s2Refusal(error: unknown): boolean {
    const err = gitErrorOf(error)
    return !err.network && err.status === 409 && err.code !== 'git_busy' && err.code !== 'invalid_state'
  }

  function commit(): Promise<void> {
    const pid = state.projectId
    const dirty = dirtyView.value
    if (!pid || !dirty || dirty.blockReason) return Promise.resolve()
    const message = state.commitMessage
    return act('dirty', 'commit', async () => {
      let result
      if (dirty.truncated) {
        const commitAll = src().baseCommitAll
        if (dirty.stage !== 2 || !commitAll || dirty.untrackedTotal === null) return
        try {
          result = await commitAll(pid, message, dirty.untrackedTotal)
        } catch (error) {
          if (s2Refusal(error)) return setNotice('dirty', { code: 'untracked_changed' })
          throw error
        }
      } else {
        // The received lists are the whole set here: tracked has no cap, untracked is not cut.
        result = await src().baseCommit(pid, message, [...dirty.tracked, ...dirty.untracked])
      }
      if (result.committed) state.commitMessage = ''
    })
  }

  function revertAll(): Promise<void> {
    const pid = state.projectId
    const dirty = dirtyView.value
    if (!pid || !dirty || dirty.blockReason) return Promise.resolve()
    return act('dirty', 'revert', async () => {
      let failed = 0
      if (dirty.truncated) {
        // S2: one request for tracked + every new file, so a total mismatch leaves the tracked
        // files unreverted too — nothing changes until the shown total is confirmed (§4.6).
        const revertAll = src().baseRevertAll
        if (dirty.stage !== 2 || !revertAll || dirty.untrackedTotal === null) return
        try {
          const reverted = await revertAll(pid, dirty.tracked, dirty.untrackedTotal)
          failed += reverted.results.filter((r) => r.result === 'error').length
        } catch (error) {
          if (s2Refusal(error)) return setNotice('dirty', { code: 'untracked_changed' })
          throw error
        }
        if (failed) setNotice('dirty', { code: 'partial_failed', params: { n: failed } })
        return
      }
      // tracked first; a failed request stops before anything is deleted.
      if (dirty.tracked.length) {
        const reverted = await src().baseRevert(pid, dirty.tracked)
        failed += reverted.results.filter((r) => r.result === 'error').length
      }
      if (dirty.untracked.length) {
        const removed = await src().baseRemove(pid, dirty.untracked)
        failed += removed.results.filter((r) => r.result === 'error').length
      }
      if (failed) setNotice('dirty', { code: 'partial_failed', params: { n: failed } })
    })
  }

  // ── Windows (§6.4) ─────────────────────────────────────────────────────────────────

  function upsertWindow(win: GitWindowDescriptor): void {
    const existing = state.windows.find((w) => w.id === win.id)
    if (existing) {
      state.windows = state.windows.map((w) => (w.id === win.id ? { ...w, title: win.title, minimized: false } : w))
    } else {
      state.windows = [...state.windows, win]
    }
  }

  function openConflictWindow(title: string): void {
    const target = activeConflict()
    if (!target) return
    if (groupCard.value !== 'ai' && !conflictActionable.value) return
    upsertWindow({
      id: conflictWindowId(target.groupId, target.mergeId),
      kind: 'conflict',
      title,
      minimized: false,
      groupId: target.groupId,
      mergeId: target.mergeId,
    })
    void loadConflicts()
  }

  function openPushErrorWindow(title: string, groupId?: string): void {
    if (!state.pushFailure && !groupId) return
    upsertWindow({ id: groupId ? `gate-job:${groupId}` : PUSH_ERROR_WINDOW_ID, kind: 'push-error', title, minimized: false, groupId: groupId ?? null, mergeId: null })
  }

  function setWindowMinimized(id: string, minimized: boolean): void {
    if (!state.windows.some((w) => w.id === id && w.minimized !== minimized)) return
    state.windows = state.windows.map((w) => (w.id === id ? { ...w, minimized } : w))
  }

  function restoreWindow(id: string): void {
    setWindowMinimized(id, false)
  }

  function closeWindow(id: string): void {
    if (!state.windows.some((w) => w.id === id)) return
    state.windows = state.windows.filter((w) => w.id !== id)
    if (!state.windows.length) dismissNotice('window')
  }

  function dispose(): void {
    disposed = true
    ctx += 1
  }

  return {
    state: readonly(state) as DeepReadonly<GitState>,
    pendingRow,
    groupCard,
    holdView,
    dirtyView,
    badge,
    workBranch,
    defaultProviderId,
    canAct,
    conflictActionable,
    setContext,
    setReadOnly,
    refresh,
    startAi,
    abortMerge,
    cancelAi,
    approve,
    openReject,
    setRejectReason,
    reject,
    applyNow,
    retryApply,
    setCommitMessage,
    commit,
    revertAll,
    ensureFileStatus,
    loadConflicts,
    openConflictWindow,
    openPushErrorWindow,
    restoreWindow,
    closeWindow,
    setWindowMinimized,
    dismissNotice,
    dispose,
  }
}

// ── Panel context ────────────────────────────────────────────────────────────────────
// DesignerGitPanel provides this to its cards and windows: the session plus the few things
// only the panel can do — raise an intent to the container, or ask a confirm question.

export interface GitConfirmRequest {
  title: string
  message: string
  confirmLabel: string
  danger?: boolean
  run: () => void
}

export interface GitPanelContext {
  session: GitSession
  openDiff(origin: GitFileOrigin, path: string, status: GitFileLetter | null): void
  openSettings(): void
  confirm(request: GitConfirmRequest): void
}

export const GIT_PANEL_CONTEXT: InjectionKey<GitPanelContext> = Symbol('designer-git-panel')

export function useGitPanel(): GitPanelContext {
  const context = inject(GIT_PANEL_CONTEXT, null)
  if (!context) throw new Error('Git cards must be used inside DesignerGitPanel.')
  return context
}
