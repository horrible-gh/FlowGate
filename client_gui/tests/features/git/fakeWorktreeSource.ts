import { vi } from 'vitest'
import type { FlowChangeSet, FlowFileDiff } from '@/features/project'
import type { GitWorktreeSource } from '@/features/git'
import type {
  ManualMergeView,
  MergeResultRow,
  MergeResultsView,
  RefsFetchResult,
  WorktreeCounts,
  WorktreeErrorDetails,
  WorktreeFile,
  WorktreeSelection,
  WorktreeStatus,
} from '@/features/git/types'
import { G, gitError } from './fakeGitSource'

// flowgate.v02.0066 — in-memory GitWorktreeSource (0066 D#2 §5-1). It hands the worktree
// session (T#6 / T#7) any E1–E12 state through the models only; the HTTP conversions are
// covered by api.0066.spec.ts. Pair it with createFakeGitSource({}, { worktree: fake.source }).

/** Detail values of an error with nothing set. */
export function noDetails(overrides: Partial<WorktreeErrorDetails> = {}): WorktreeErrorDetails {
  return {
    reason: null,
    jobId: null,
    mergeId: null,
    latestMergeId: null,
    sessionKind: null,
    targetFastForwarded: null,
    pushVia: null,
    count: null,
    counts: {},
    protectedUncommitted: null,
    ...overrides,
  }
}

/** A listed worktree file: an existing path with an unstaged change, stageable. */
export function worktreeFile(path: string, overrides: Partial<WorktreeFile> = {}): WorktreeFile {
  return {
    path,
    inHead: true,
    staged: null,
    unstaged: 'M',
    untracked: false,
    fileClass: 'normal',
    protectedReason: null,
    unsupported: null,
    canStage: true,
    canUnstage: false,
    canRevert: true,
    canRemove: false,
    insertions: 1,
    deletions: 0,
    ...overrides,
  }
}

/** An E1 status whose counts follow `files` (pass `counts` to state other server values). */
export function worktreeStatus(overrides: Partial<WorktreeStatus> = {}): WorktreeStatus {
  const { files: given, counts, ...rest } = overrides
  const files = given ?? [worktreeFile('src/a.ts')]
  return {
    groupId: G,
    branch: 'flowgate_v02_0008',
    head: 'h1',
    worktreeAvailable: true,
    worktreeToken: 'wt-1',
    indexToken: 'ix-1',
    gitState: { status: 'none', rootWfDone: false, finalApprovalBound: false },
    floor: {
      workBaseRef: 'v0.2',
      scopeBaseSha: 'f1',
      workBaseSha: 'f1',
      workBaseSyncSha: null,
      workBaseState: 'verified',
      error: null,
    },
    committed: { commitCount: 0, changedFileCount: 0 },
    ownership: { available: true, reason: null },
    blockers: [],
    ...rest,
    files,
    counts: counts ?? {
      changed: files.length,
      staged: files.filter((f) => f.staged).length,
      unstaged: files.filter((f) => f.unstaged).length,
      untracked: files.filter((f) => f.untracked).length,
      excludedNewUntracked: 0,
      hidden: 0,
      managedDirty: 0,
      unsupported: 0,
    },
  }
}

/** An E8 view: the work base is the default target and the merge is admitted. */
export function manualMergeView(overrides: Partial<ManualMergeView> = {}): ManualMergeView {
  return {
    canMerge: true,
    blocked: [],
    admissionBasis: 'wf_done',
    approval: { acDocId: null, status: null, finalApprovalBound: false },
    defaultTarget: 'v0.2',
    targets: [
      { name: 'v0.2', kind: 'base', eligible: true, blockedReason: null, isWorkBase: true },
      { name: 'release', kind: 'local', eligible: true, blockedReason: null, isWorkBase: false },
    ],
    preview: {
      targetBranch: 'v0.2',
      workBaseRef: 'v0.2',
      projectBaseBranch: 'v0.2',
      isWorkBase: true,
      isProjectBase: true,
      incomingWorkBaseCommits: null,
      warnings: [],
      error: null,
    },
    commitCount: 1,
    floorError: null,
    aheadOfTarget: 1,
    mergeNeeded: true,
    recovery: false,
    uncommitted: { blocking: 0, counts: {}, protectedUncommitted: 0, reason: null },
    graph: { runPresent: false, blocker: null, gateOpen: false },
    ...overrides,
  }
}

export function mergeResultRow(overrides: Partial<MergeResultRow> = {}): MergeResultRow {
  return {
    mergeId: 11,
    target: { targetBranch: 'v0.2', isProjectBase: true },
    mergeCommit: 'a'.repeat(40),
    pushed: false,
    recovered: false,
    completedAt: '2026-10-16T10:21:00+09:00',
    remote: { measured: true, remoteBranchMissing: false, localAheadCount: 1, containsMergeCommit: false },
    pushAvailable: true,
    pushBlockedReason: null,
    ...overrides,
  }
}

export function mergeResults(overrides: Partial<MergeResultsView> = {}): MergeResultsView {
  const results = overrides.results ?? []
  return {
    groupStatus: 'none',
    results,
    pushCandidates: results.filter((r) => r.pushAvailable).length,
    groupBlocker: null,
    ...overrides,
  }
}

export interface FakeWorktreeState {
  status: WorktreeStatus
  merge: ManualMergeView
  results: MergeResultsView
  targetChanges: Record<string, FlowChangeSet>
  // flowgate.v02.0067 T#4 — group file diffs by path (the `Diff` tab detail); others get fileDiff().
  fileDiffs: Record<string, FlowFileDiff>
}

/** A group file diff: one changed line, `old <path>` → `new <path>`. */
export function fileDiff(path: string, overrides: Partial<FlowFileDiff> = {}): FlowFileDiff {
  const side = (content: string) => ({ exists: true, binary: false, truncated: false, size: content.length, content })
  return {
    groupId: G,
    branch: 'flowgate_v02_0008',
    commit: null,
    baseBranch: 'v0.2',
    mergeBase: 'f1',
    path,
    status: 'M',
    old: side(`old ${path}\nsame\n`),
    new: side(`new ${path}\nsame\n`),
    ...overrides,
  }
}

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

// ── Worktree transitions (server worktree_manual / manual_path_policy, 0066 D#1 §3-2–§3-5) ──
// A listed row is HEAD / index / working file seen through its letters: `staged` = HEAD vs index,
// `unstaged` = index vs working file, `untracked` = a working file the index does not have.
// Every operation changes only the side the server changes, and a row with no change left is
// no longer listed. Hidden entries are never listed, so the fake only carries their counts.

// What the server recomputes for a row it touched (manual_path_policy.classify).
function classified(file: WorktreeFile): WorktreeFile {
  const unsupported =
    file.staged === 'U' ? 'unmerged' : file.staged === 'T' || file.unstaged === 'T' ? 'type_change' : null
  const protectedReason =
    file.fileClass === 'unverified'
      ? 'ownership_unavailable'
      : file.fileClass === 'managed'
        ? 'managed'
        : file.fileClass === 'excluded'
          ? file.inHead
            ? null
            : 'excluded_new'
          : file.fileClass === 'normal'
            ? null
            : file.protectedReason
  const mutable = protectedReason === null && unsupported === null
  return {
    ...file,
    unsupported,
    protectedReason,
    canStage: mutable,
    canUnstage: !!file.staged,
    canRevert: mutable && file.inHead,
    canRemove: mutable && !file.inHead,
  }
}

const hasChange = (file: WorktreeFile) => !!file.staged || !!file.unstaged || file.untracked
// An untracked new file under an exclusion rule is counted, not listed.
const listed = (file: WorktreeFile) =>
  !(file.fileClass === 'excluded' && !file.inHead && file.untracked && !file.staged)

// Stage: the index takes the working file (a missing working file stages the deletion).
function staged(file: WorktreeFile): WorktreeFile | null {
  if (file.untracked) return { ...file, staged: file.inHead ? 'M' : 'A', unstaged: null, untracked: false }
  if (file.unstaged === 'D') return file.inHead ? { ...file, staged: 'D', unstaged: null } : null
  if (file.unstaged) return { ...file, staged: file.inHead ? 'M' : 'A', unstaged: null }
  return file
}

// Unstage: the index goes back to HEAD; the working file stays as it is.
function unstaged(file: WorktreeFile): WorktreeFile | null {
  if (!file.staged) return file
  if (!file.inHead) return file.unstaged === 'D' ? null : { ...file, staged: null, unstaged: null, untracked: true }
  const workingGone = file.staged === 'D' ? !file.untracked : file.unstaged === 'D'
  const work = workingGone ? 'D' : (file.unstaged ?? (file.staged === 'T' ? 'T' : 'M'))
  return { ...file, staged: null, unstaged: work, untracked: false }
}

// Commit: HEAD takes the whole index; the unstaged part of the same file stays.
function committed(file: WorktreeFile): WorktreeFile | null {
  if (!file.staged) return file
  if (file.staged === 'D') return file.untracked ? { ...file, inHead: false, staged: null } : null
  return { ...file, inHead: true, staged: null }
}

interface Settled {
  files: WorktreeFile[]
  // Rows that became an unlisted excluded new file.
  unlisted: number
}

// Applies `step` to the chosen rows, reclassifies what changed and drops rows with no change left.
function settle(
  files: WorktreeFile[],
  chosen: (file: WorktreeFile) => boolean,
  step: (file: WorktreeFile) => WorktreeFile | null,
): Settled {
  const result: WorktreeFile[] = []
  let unlisted = 0
  for (const file of files) {
    const next = chosen(file) ? step(file) : file
    if (next === file) result.push(file)
    else if (next && hasChange(next)) {
      if (listed(next)) result.push(classified(next))
      else unlisted += 1
    }
  }
  return { files: result, unlisted }
}

// E1 counts after a change: listed rows are recounted; what the server counted beyond the listed
// rows (hidden / unlisted entries) carries over, except the hidden Stage a full unstage released.
function countsAfter(before: WorktreeStatus, after: Settled, releasedHiddenStage: number): WorktreeCounts {
  const of = (files: WorktreeFile[]) => ({
    staged: files.filter((f) => f.staged).length,
    unstaged: files.filter((f) => f.unstaged).length,
    untracked: files.filter((f) => f.untracked).length,
    managedDirty: files.filter((f) => f.fileClass === 'managed').length,
    unsupported: files.filter((f) => f.unsupported).length,
  })
  const was = of(before.files)
  const now = of(after.files)
  const beyond = (key: keyof typeof was) => Math.max(0, (before.counts[key] ?? 0) - was[key])
  return {
    changed: after.files.length,
    staged: now.staged + beyond('staged') - releasedHiddenStage,
    unstaged: now.unstaged + beyond('unstaged'),
    untracked: now.untracked + beyond('untracked') + after.unlisted,
    excludedNewUntracked: (before.counts.excludedNewUntracked ?? 0) + after.unlisted,
    hidden: before.counts.hidden ?? 0,
    managedDirty: now.managedDirty + beyond('managedDirty'),
    unsupported: now.unsupported + beyond('unsupported'),
  }
}

// Staged entries the server counted but did not list (hidden ones).
const unlistedStage = (status: WorktreeStatus) =>
  Math.max(0, (status.counts.staged ?? 0) - status.files.filter((f) => f.staged).length)

const toSlashes = (paths: string[]) => paths.map((p) => p.replace(/\\/g, '/'))

/**
 * In-memory GitWorktreeSource. Stage / unstage / commit / revert / remove change its own E1 state
 * the way the server does and refuse what the server refuses (stale token, path_protected,
 * nothing_staged, new_path_use_remove / existing_path_use_revert, …).
 * The fingerprints follow the result: a changed index or HEAD gives a new `indexToken` and
 * `worktreeToken`, a changed working file only a new `worktreeToken`, a no-op keeps both.
 * Generated tokens are `ix-2` / `wt-2`, …; a commit's HEAD is its `commitSha`.
 */
export function createFakeWorktreeSource(init: Partial<FakeWorktreeState> = {}, extra: Partial<GitWorktreeSource> = {}) {
  const db: FakeWorktreeState = {
    status: worktreeStatus(),
    merge: manualMergeView(),
    results: mergeResults(),
    targetChanges: {},
    fileDiffs: {},
    ...init,
  }
  let generation = 1
  let commits = 0

  const admit = (token: { worktree?: string | null; index?: string | null }) => {
    if (!db.status.worktreeAvailable) throw gitError(409, 'invalid_state')
    if (token.worktree != null && token.worktree !== db.status.worktreeToken) throw gitError(409, 'worktree_changed')
    if (token.index != null && token.index !== db.status.indexToken) throw gitError(409, 'worktree_changed')
  }
  const rowOf = (path: string) => db.status.files.find((f) => f.path === path)

  // Stores the new state; `index` = the index or HEAD changed, `work` = a working file changed.
  const store = (
    after: Settled,
    change: { index: boolean; work: boolean },
    fields: Partial<WorktreeStatus> = {},
    releasedHiddenStage = 0,
  ) => {
    const before = db.status
    const next: WorktreeStatus = {
      ...before,
      ...fields,
      files: after.files,
      counts: countsAfter(before, after, releasedHiddenStage),
    }
    if (change.index || change.work) {
      generation += 1
      next.worktreeToken = `wt-${generation}`
      if (change.index) next.indexToken = `ix-${generation}`
    }
    db.status = next
    return clone(next)
  }

  const changeIndex = (selection: WorktreeSelection, worktreeToken: string | null, unstage: boolean) => {
    admit({ worktree: worktreeToken })
    const before = db.status
    const all = 'all' in selection
    const paths = all ? [] : toSlashes(selection.paths)
    const refused = paths.some((path) => {
      const row = rowOf(path)
      return !row || (unstage ? !row.staged : !row.canStage)
    })
    if (refused) throw gitError(409, 'path_protected')
    const chosen = (f: WorktreeFile) => (all ? (unstage ? !!f.staged : f.canStage) : paths.includes(f.path))
    // The index changes only for rows whose index differs from the side it takes.
    const index = before.files.some((f) => chosen(f) && (unstage ? !!f.staged : !!f.unstaged || f.untracked))
    const released = unstage && all ? unlistedStage(before) : 0
    const skipped = { managed: 0, hidden: 0, excludedNew: 0, unsupported: 0 }
    if (all && !unstage) {
      skipped.hidden = before.counts.hidden ?? 0
      skipped.excludedNew = before.counts.excludedNewUntracked ?? 0
      for (const f of before.files.filter((row) => !row.canStage)) {
        if (f.unsupported) skipped.unsupported += 1
        else if (f.fileClass === 'managed') skipped.managed += 1
        else skipped.excludedNew += 1
      }
    }
    const applied = before.files.filter(chosen).map((f) => f.path)
    const after = settle(before.files, chosen, unstage ? unstaged : staged)
    const status = store(after, { index: index || released > 0, work: false }, {}, released)
    return { applied, appliedHiddenCount: released, skipped, status, statusError: null }
  }

  const changeFiles = (paths: string[], worktreeToken: string | null, remove: boolean) => {
    admit({ worktree: worktreeToken })
    const selected = toSlashes(paths)
    const rows = selected.map(rowOf)
    if (rows.some((row) => row && (row.inHead ? !row.canRevert : !row.canRemove))) throw gitError(409, 'path_protected')
    if (remove && rows.some((row) => row?.inHead)) throw gitError(422, 'existing_path_use_revert')
    if (!remove && rows.some((row) => row && !row.inHead)) throw gitError(422, 'new_path_use_remove')
    const chosen = (f: WorktreeFile) => selected.includes(f.path)
    const touched = db.status.files.filter(chosen)
    // Revert puts index and working file back to HEAD, remove drops both: the index changes only
    // where something was staged, the working file wherever it differed or existed.
    const index = touched.some((f) => !!f.staged)
    const work = touched.some((f) => (remove ? f.unstaged !== 'D' : true))
    const status = store(settle(db.status.files, chosen, () => null), { index, work })
    const done = remove ? 'removed' : 'reverted'
    return {
      results: selected.map((path) => ({ path, result: touched.some((f) => f.path === path) ? done : 'not_changed' })),
      status,
      statusError: null,
    }
  }

  const source = {
    loadStatus: vi.fn(async () => clone(db.status)),
    stage: vi.fn(async (_gid: string, selection: WorktreeSelection, worktreeToken: string | null = null) =>
      changeIndex(selection, worktreeToken, false),
    ),
    unstage: vi.fn(async (_gid: string, selection: WorktreeSelection, worktreeToken: string | null = null) =>
      changeIndex(selection, worktreeToken, true),
    ),
    commit: vi.fn(async (_gid: string, message: string, indexToken: string | null = null) => {
      admit({ index: indexToken })
      const subject = message.trim()
      if (!subject) throw gitError(422, 'commit_message_required')
      const before = db.status
      const stagedRows = before.files.filter((f) => f.staged)
      const hiddenStage = unlistedStage(before)
      if (!stagedRows.length && !hiddenStage) throw gitError(422, 'nothing_staged')
      if (stagedRows.some((f) => f.staged === 'U' || f.staged === 'T')) throw gitError(409, 'index_entry_unsupported')
      if (hiddenStage || stagedRows.some((f) => f.protectedReason === 'managed' || f.protectedReason === 'excluded_new')) {
        throw gitError(409, 'staged_protected_paths')
      }
      commits += 1
      const commitSha = `c${commits}`.padEnd(40, 'c')
      const committedPaths = stagedRows.map((f) => f.path)
      const range = before.committed
      const status = store(
        settle(before.files, (f) => !!f.staged, committed),
        // A new HEAD is a new index fingerprint, and the worktree fingerprint contains it.
        { index: true, work: false },
        {
          head: commitSha,
          // Display only; assumes the committed paths were not in the range yet.
          committed: range && {
            commitCount: (range.commitCount ?? 0) + 1,
            changedFileCount: (range.changedFileCount ?? 0) + committedPaths.length,
          },
        },
      )
      return {
        commitSha,
        commit: commitSha.slice(0, 7),
        subject,
        committedPaths,
        committedCount: committedPaths.length,
        status,
        statusError: null,
      }
    }),
    revert: vi.fn(async (_gid: string, paths: string[], worktreeToken: string | null = null) =>
      changeFiles(paths, worktreeToken, false),
    ),
    remove: vi.fn(async (_gid: string, paths: string[], worktreeToken: string | null = null) =>
      changeFiles(paths, worktreeToken, true),
    ),
    suggestCommitMessage: vi.fn(async (_gid: string, providerId: string) => ({
      suggested: 'feat(git): draft subject',
      providerId,
      stagedCount: db.status.counts.staged,
      inputTruncated: false,
      head: db.status.head,
      indexToken: db.status.indexToken,
    })),
    loadManualMerge: vi.fn(async () => clone(db.merge)),
    manualMerge: vi.fn(async (_gid: string, target: string | null, push: boolean) => ({
      status: 'merged',
      groupStatus: 'none',
      worktreeKept: true,
      action: push ? 'merge' : 'merge_only',
      targetBranch: target ?? db.merge.defaultTarget,
      mergeCommit: 'aaaaaaa',
      mergeCommitSha: 'a'.repeat(40),
      pushed: push,
      recovered: false,
      mergeId: 11,
      conflictFileCount: 0,
      excludedArtifactCount: 0,
      jobId: 'job-1',
    })),
    loadMergeResults: vi.fn(async () => clone(db.results)),
    pushMergeResult: vi.fn(async (_gid: string, mergeId: number, mergeCommit: string) => {
      const row = db.results.results.find((r) => r.mergeId === mergeId)
      return {
        pushedRef: row ? `refs/heads/${row.target.targetBranch}` : null,
        pushedHead: mergeCommit,
        targetBranch: row?.target.targetBranch ?? null,
        isProjectBase: row?.target.isProjectBase ?? false,
        mergeId,
        mergeCommit,
        alreadyPublished: false,
        jobId: 'job-2',
        phase: 'done',
      }
    }),
    fetchRefs: vi.fn(
      async (): Promise<RefsFetchResult> => ({ fetched: true, baseBranch: 'v0.2', aheadCount: 0, behindCount: 0, group: null }),
    ),
    loadTargetChanges: vi.fn(async (_pid: string, groupId: string, target: string): Promise<FlowChangeSet> =>
      clone(db.targetChanges[target] ?? { groupId, branch: 'flowgate_v02_0008', commit: 'h1', baseBranch: target, changes: [] }),
    ),
    loadFileDiff: vi.fn(async (_pid: string, groupId: string, path: string, compareBase: string | null): Promise<FlowFileDiff> =>
      clone(db.fileDiffs[path] ?? fileDiff(path, { groupId, baseBranch: compareBase ?? db.status.floor.workBaseRef ?? 'v0.2' })),
    ),
    ...extra,
  } satisfies GitWorktreeSource
  return { db, source }
}
