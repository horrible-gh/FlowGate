import { vi } from 'vitest'
import type { FlowAiActivity, ProjectGitPendingRow, ProjectGitStatus } from '@/features/project'
import type { GitSource } from '@/features/git'
import type { GitConflictSession, GitProviders, GitReview } from '@/features/git/types'

// flowgate.v02.0033 — in-memory GitSource. Every server state the Git part reacts to, including
// the S1 / S2 stage-2 fields and a push_rejected answer, is replayed through it (0033-D §5.1).

export const P = 'p'
export const G = 'p.m.0001'

/** A Git error the way axios hands it over (normalizeGitError reads response.status/data). */
export function gitError(status: number, code: string): Error {
  return Object.assign(new Error(code), {
    isAxiosError: true,
    response: { status, data: { ok: false, error: { code, message: 'server text never shown' } } },
  })
}

export function networkError(): Error {
  return Object.assign(new Error('Network Error'), { isAxiosError: true })
}

export function status(overrides: Partial<ProjectGitStatus> = {}): ProjectGitStatus {
  return {
    enabled: true,
    baseBranch: 'v0.2',
    slots: [{ groupId: G, branch: 'flowgate_v02_0008', status: 'active', writable: true }],
    pending: [],
    baseDirty: { files: [], mergeInProgress: false, aiRun: false },
    baseUntracked: { files: [], truncated: false, total: null },
    ...overrides,
  }
}

export function row(overrides: Partial<ProjectGitPendingRow> = {}): ProjectGitPendingRow {
  return {
    groupId: G,
    status: 'conflict',
    mergeId: 7,
    acDocId: 'p.m.0001.0009-AC',
    reviewState: null,
    finalApprovalBound: false,
    plan: null,
    ...overrides,
  }
}

export function conflicts(count = 5, resolved = 0): GitConflictSession {
  return {
    mergeId: 7,
    branch: 'flowgate_v02_0008',
    baseBranch: 'v0.2',
    targetBranch: 'main',
    trConflict: false,
    files: Array.from({ length: count }, (_, i) => ({ path: `src/f${i}.ts`, conflictCount: i < resolved ? 0 : 2 })),
  }
}

export function review(overrides: Partial<GitReview> = {}): GitReview {
  return {
    mergeId: 7,
    reviewState: 'resolved_pending_review',
    fingerprint: 'fp-1',
    changes: [
      { path: 'src/a.ts', status: 'M' },
      { path: 'src/b.ts', status: 'A' },
    ],
    resolverProvider: 'Claude Opus 5',
    reconciliationKind: null,
    canApprove: true,
    canReject: true,
    ...overrides,
  }
}

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

const idle: FlowAiActivity = { active: false, runId: null, status: null, actionScope: null }
export const resolvingRun: FlowAiActivity = { active: true, runId: 'run-1', status: 'running', actionScope: 'resolve_conflict' }

export interface FakeState {
  status: ProjectGitStatus | null
  ai: FlowAiActivity
  conflicts: GitConflictSession
  review: GitReview
  providers: GitProviders
  fileLetters: Record<string, 'A' | 'D' | 'M'>
}

export function createFakeGitSource(init: Partial<FakeState> = {}, extra: Partial<GitSource> = {}) {
  const db: FakeState = {
    status: status(),
    ai: idle,
    conflicts: conflicts(),
    review: review(),
    providers: { providerIds: ['opus', 'sonnet'], defaultProviderId: 'opus' },
    fileLetters: {},
    ...init,
  }
  const source = {
    loadStatus: vi.fn(async () => (db.status ? clone(db.status) : null)),
    loadAiActivity: vi.fn(async () => ({ ...db.ai })),
    loadBaseFileDiff: vi.fn(async (_pid: string, path: string) => {
      const letter = db.fileLetters[path]
      if (!letter) throw gitError(404, 'not_found')
      const side = { exists: true, binary: false, truncated: false, size: 1, content: 'x' }
      return { branch: 'v0.2', commit: 'abc', path, status: letter, old: side, new: side }
    }),
    loadReviewFileDiff: vi.fn(async () => {
      throw new Error('unused')
    }),
    fetchConflicts: vi.fn(async () => clone(db.conflicts)),
    startConflictAi: vi.fn(async () => 'run-1'),
    cancelAiRun: vi.fn(async () => 'cancelling'),
    abortMerge: vi.fn(async () => undefined),
    fetchReview: vi.fn(async () => clone(db.review)),
    approveReview: vi.fn(async () => ({ status: 'merged', reviewState: 'completed', reconciliationKind: null, errorCount: 0 })),
    rejectReview: vi.fn(async () => ({ status: 'returned_to_resolver', resolverRunId: 'run-2' })),
    fetchProviders: vi.fn(async () => clone(db.providers)),
    finalize: vi.fn(async () => ({ status: 'merged' })),
    push: vi.fn(async () => ({ status: 'pushed' })),
    baseCommit: vi.fn(async () => ({ committed: true, remaining: [], remainingUntracked: [] })),
    baseRevert: vi.fn(async (_pid: string, files: string[]) => ({
      results: files.map((path) => ({ path, result: 'reverted' })),
      remaining: [],
    })),
    baseRemove: vi.fn(async (_pid: string, files: string[]) => ({
      results: files.map((path) => ({ path, result: 'removed' })),
      remainingUntracked: [],
    })),
    ...extra,
  } satisfies GitSource
  return { db, source }
}

/** The conflicts answer of a TR revert session as the server sends it today (kind tr_revert). */
export function trConflicts(): GitConflictSession {
  return { ...conflicts(2), branch: null, targetBranch: null, trConflict: true }
}

/** A sleep that never resolves on its own: polling waits until the test releases it. */
export function manualSleep() {
  const waiters: Array<() => void> = []
  return {
    sleep: vi.fn(() => new Promise<void>((resolve) => waiters.push(resolve))),
    tick() {
      waiters.splice(0).forEach((resolve) => resolve())
    },
    get pending() {
      return waiters.length
    },
  }
}
