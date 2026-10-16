import { describe, expect, it } from 'vitest'
import type { ManualMergeView, WorktreeStatus } from '@/features/git/types'
import {
  mergeBlockReason,
  reasonMessage,
  worktreeReason,
  type WorktreeButton,
  type WorktreeReasonInput,
} from '@/features/git/worktreeReasons'
import { manualMergeView, mergeResultRow, mergeResults, noDetails, worktreeStatus } from './fakeWorktreeSource'

// flowgate.v02.0066 T#7 — C21 for the merge / follow-up push / Fetch columns of 0066 D#2 §3-5:
// read-only > 403 > in progress > checking > no workspace > (E8 read failed) > no target > the
// first E8 `blocked[]` entry; push: the group blocker or the row's reason; Fetch: ranks 1–3 only.
// A waiting approval is never a reason (C17).

function input(overrides: Partial<WorktreeReasonInput> = {}, merge: Partial<ManualMergeView> = {}): WorktreeReasonInput {
  return {
    readOnly: false,
    forbidden: false,
    busy: false,
    status: worktreeStatus(),
    sessionCard: false,
    hiddenStaged: 0,
    stagedProtectedRows: 0,
    message: '',
    providerId: null,
    merge: { checking: false, failed: false, target: 'v0.2', view: manualMergeView(merge) },
    push: { results: mergeResults({ results: [mergeResultRow()] }), row: mergeResultRow() },
    ...overrides,
  }
}

const APPLY: WorktreeButton[] = ['merge', 'push', 'fetch']
const codes = (i: WorktreeReasonInput) => APPLY.map((b) => worktreeReason(b, i).code)
const block = (code: string, details = noDetails()) => ({ code, reason: details.reason, details })

describe('worktreeReasons — merge / push / Fetch columns (C21)', () => {
  it('enabled when nothing applies', () => {
    expect(APPLY.map((b) => worktreeReason(b, input()))).toEqual(APPLY.map(() => ({ disabled: false, code: null })))
  })

  it('read-only > 403 > in progress on all three', () => {
    const all = input({ readOnly: true, forbidden: true, busy: true })
    expect(codes(all)).toEqual(['read_only', 'read_only', 'read_only'])
    expect(codes({ ...all, readOnly: false })).toEqual(['forbidden', 'forbidden', 'forbidden'])
    expect(APPLY.map((b) => worktreeReason(b, { ...all, readOnly: false, forbidden: false }))).toEqual(
      APPLY.map(() => ({ disabled: true, code: null })),
    )
  })

  it('merge: checking (no text) > no workspace > read failed > no target > first E8 block > can_merge false', () => {
    const base = input({}, { canMerge: false, blocked: [block('git_busy'), block('nothing_to_merge')] })
    const checking = { ...base, merge: { ...base.merge!, checking: true }, status: worktreeStatus({ worktreeAvailable: false }) }
    expect(worktreeReason('merge', checking)).toEqual({ disabled: true, code: null })
    const noWorkspace = { ...checking, merge: { ...checking.merge!, checking: false, failed: true } }
    expect(worktreeReason('merge', noWorkspace).code).toBe('worktree_unavailable')
    const failed = { ...noWorkspace, status: worktreeStatus() }
    expect(worktreeReason('merge', failed).code).toBe('merge_unread')
    const noTarget = { ...failed, merge: { ...failed.merge!, failed: false, target: null } }
    expect(worktreeReason('merge', noTarget).code).toBe('no_target')
    const ineligible = input({}, { targets: [{ name: 'v0.2', kind: 'base', eligible: false, blockedReason: 'owner_mismatch', isWorkBase: true }] })
    expect(worktreeReason('merge', ineligible).code).toBe('no_target')
    // The first block in the server's order.
    expect(worktreeReason('merge', { ...failed, merge: { ...failed.merge!, failed: false } }).code).toBe('git_busy')
    expect(worktreeReason('merge', input({}, { canMerge: false })).code).toBe('merge_unavailable')
  })

  it('E1 blockers and the 0033 card are not merge / push / Fetch reasons (the E8 / E10 answers carry them)', () => {
    const status: WorktreeStatus = worktreeStatus({
      blockers: [{ code: 'run_already_active', operations: ['stage', 'unstage', 'commit', 'revert', 'remove'] }],
    })
    expect(codes(input({ status, sessionCard: true }))).toEqual([null, null, null])
  })

  it('C17: a pending review, a final-approval binding or an open gate is never a reason', () => {
    const waiting = input(
      {},
      {
        approval: { acDocId: 'x-AC', status: 'pending_review', finalApprovalBound: true },
        graph: { runPresent: true, blocker: null, gateOpen: true },
        admissionBasis: 'no_wf_done',
      },
    )
    expect(worktreeReason('merge', waiting)).toEqual({ disabled: false, code: null })
  })

  it('E8 block texts: uncommitted (+ protected), already recorded, target codes, an open session', () => {
    const view = manualMergeView({ uncommitted: { blocking: 3, counts: {}, protectedUncommitted: 0, reason: null } })
    expect(mergeBlockReason(block('uncommitted_changes', noDetails({ count: 9 })), view)).toEqual({
      disabled: true,
      code: 'uncommitted_changes',
      params: { n: 3, protected: 0 },
    })
    expect(mergeBlockReason(block('uncommitted_changes', noDetails({ count: 4, protectedUncommitted: 2 })), null)).toEqual({
      disabled: true,
      code: 'uncommitted_protected',
      params: { n: 4, protected: 2 },
    })
    expect(mergeBlockReason(block('nothing_to_merge', noDetails({ reason: 'already_recorded' })), null).code).toBe('already_recorded')
    expect(mergeBlockReason(block('nothing_to_merge'), null).code).toBe('nothing_to_merge')
    expect(mergeBlockReason(block('merge_target_remote_only'), null)).toEqual({
      disabled: true,
      code: 'merge_target',
      params: { code: 'merge_target_remote_only' },
    })
    expect(mergeBlockReason(block('invalid_state', noDetails({ sessionKind: 'tr_revert' })), null).code).toBe('session_open')
    expect(mergeBlockReason(block('invalid_state'), null).code).toBe('invalid_state')
  })

  it('push: no answer yet (no text) > group blocker > the row reason; no workspace is not a push reason', () => {
    expect(worktreeReason('push', input({ push: { results: null, row: null } }))).toEqual({ disabled: true, code: null })
    const blocked = mergeResults({ groupBlocker: 'conflict', results: [mergeResultRow({ pushAvailable: false, pushBlockedReason: 'group_blocked' })] })
    expect(worktreeReason('push', input({ push: { results: blocked, row: null } })).code).toBe('group_blocked')
    const unmeasured = mergeResults({ results: [mergeResultRow({ pushAvailable: false, pushBlockedReason: 'unmeasured' })] })
    expect(worktreeReason('push', input({ push: { results: unmeasured, row: null } })).code).toBe('unmeasured')
    const published = mergeResults({ results: [mergeResultRow({ pushAvailable: false, pushBlockedReason: 'already_published' })] })
    expect(worktreeReason('push', input({ push: { results: published, row: null } })).code).toBe('no_push_candidate')
    const noWorkspace = input({ status: worktreeStatus({ worktreeAvailable: false }) })
    expect([worktreeReason('push', noWorkspace).code, worktreeReason('fetch', noWorkspace).code]).toEqual([null, null])
  })

  it('reason texts carry their values as strings; unknown codes read "사용할 수 없음({code})"', () => {
    expect(reasonMessage({ disabled: true, code: 'uncommitted_changes', params: { n: 3, protected: 0 } })).toEqual({
      key: 'gui.git.reason.uncommitted_changes',
      params: { n: '3', protected: '0' },
    })
    expect(reasonMessage({ disabled: true, code: 'no_target' })).toEqual({ key: 'gui.git.reason.no_target', params: {} })
    expect(reasonMessage({ disabled: true, code: 'mystery' })).toEqual({ key: 'gui.git.reason.unknown', params: { code: 'mystery' } })
  })
})
