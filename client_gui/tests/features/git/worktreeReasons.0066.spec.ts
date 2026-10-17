import { describe, expect, it } from 'vitest'
import type { WorktreeStatus } from '@/features/git/types'
import { reasonMessage, worktreeReason, type WorktreeButton, type WorktreeReasonInput } from '@/features/git/worktreeReasons'
import { worktreeFile, worktreeStatus } from './fakeWorktreeSource'

// flowgate.v02.0066 T#6 — C21: one disabled reason per button, in the order of 0066 D#2 §3-5
// (file and commit columns; merge / push / fetch are T#7's).
const stagedFile = worktreeFile('src/a.ts', { staged: 'M', unstaged: null, canStage: false, canUnstage: true })

function input(overrides: Partial<WorktreeReasonInput> = {}, status: Partial<WorktreeStatus> = {}): WorktreeReasonInput {
  return {
    readOnly: false,
    forbidden: false,
    busy: false,
    status: worktreeStatus({ files: [stagedFile], ...status }),
    sessionCard: false,
    hiddenStaged: 0,
    stagedProtectedRows: 0,
    message: 'feat: x',
    providerId: 'opus',
    ...overrides,
  }
}

const BUTTONS: WorktreeButton[] = ['stage', 'unstage', 'unstageAll', 'commit', 'ai']
const codes = (i: WorktreeReasonInput) => BUTTONS.map((b) => worktreeReason(b, i).code)
const blocker = (code: string, operations: string[]) => ({ code, operations })

describe('worktreeReasons — priority (C21)', () => {
  it('enabled when nothing applies', () => {
    expect(BUTTONS.map((b) => worktreeReason(b, input()).disabled)).toEqual([false, false, false, false, false])
  })

  it('read-only > 403 > in progress > no workspace > session > blockers > staged rules, one reason each', () => {
    // Every rule at once: the first one wins on every button.
    const all = input(
      { readOnly: true, forbidden: true, busy: true, sessionCard: true, hiddenStaged: 1, message: '', providerId: null },
      { worktreeAvailable: false, blockers: [blocker('run_already_active', ['stage', 'unstage', 'commit', 'revert', 'remove'])] },
    )
    expect(codes(all)).toEqual(['read_only', 'read_only', 'read_only', 'read_only', 'read_only'])
    expect(codes({ ...all, readOnly: false })).toEqual(['forbidden', 'forbidden', 'forbidden', 'forbidden', 'forbidden'])
    const busy = { ...all, readOnly: false, forbidden: false }
    expect(BUTTONS.map((b) => worktreeReason(b, busy))).toEqual(BUTTONS.map(() => ({ disabled: true, code: null })))
    const noWorkspace = { ...busy, busy: false }
    expect(codes(noWorkspace)).toEqual(Array(5).fill('worktree_unavailable'))
    const session = { ...noWorkspace, status: { ...(noWorkspace.status as WorktreeStatus), worktreeAvailable: true } }
    expect(codes(session)).toEqual(Array(5).fill('session_open'))
    // The E1 blocker session_open is the same rule as the 0033 card.
    const e1Session = input({}, { blockers: [blocker('session_open', ['stage', 'unstage', 'commit', 'revert', 'remove'])] })
    expect(codes(e1Session)).toEqual(Array(5).fill('session_open'))
  })

  it('an E1 blocker applies only to the operations it names (a full unstage stays open)', () => {
    const ownership = input({}, { blockers: [blocker('ownership_unavailable', ['stage', 'commit', 'revert', 'remove', 'suggest'])] })
    expect(codes(ownership)).toEqual(['ownership_unavailable', null, null, 'ownership_unavailable', 'ownership_unavailable'])
    const run = input({}, { blockers: [blocker('run_already_active', ['stage', 'unstage', 'commit', 'revert', 'remove'])] })
    expect(codes(run)).toEqual(['run_already_active', 'run_already_active', 'run_already_active', 'run_already_active', null])
    // The first blocker aimed at the operation, in the server's order.
    const two = input({}, {
      blockers: [blocker('group_lease_active', ['commit']), blocker('branch_merge_claim_active', ['stage', 'commit'])],
    })
    expect(codes(two)).toEqual(['branch_merge_claim_active', null, null, 'group_lease_active', null])
  })

  it('commit / AI: no Stage > protected Stage > message (commit) / provider (AI)', () => {
    const empty = input({ message: '', providerId: null }, { files: [worktreeFile('a.ts')] })
    expect(codes(empty)).toEqual([null, null, null, 'no_staged', 'no_staged'])
    const hidden = input({ hiddenStaged: 1, message: '', providerId: null })
    expect(codes(hidden)).toEqual([null, null, null, 'staged_protected', 'staged_protected'])
    const listed = input({ stagedProtectedRows: 1, message: '' })
    expect(codes(listed)).toEqual([null, null, null, 'staged_protected', 'staged_protected'])
    expect(codes(input({ message: '   ', providerId: null }))).toEqual([null, null, null, 'no_message', 'no_provider'])
  })

  it('the reason text key: own text for known codes, "사용할 수 없음({code})" otherwise, none while in progress', () => {
    expect(reasonMessage({ disabled: true, code: 'read_only' })).toEqual({ key: 'gui.git.reason.read_only', params: {} })
    expect(reasonMessage({ disabled: true, code: 'brand_new_code' })).toEqual({ key: 'gui.git.reason.unknown', params: { code: 'brand_new_code' } })
    expect(reasonMessage({ disabled: true, code: null })).toBeNull()
    expect(reasonMessage({ disabled: false, code: null })).toBeNull()
  })
})
