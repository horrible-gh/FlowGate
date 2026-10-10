/**
 * flowgate.v02.0048 T#12 — checkModel as tables (D#1 §8.5): rows are input combinations, columns the
 * operations' visible / enabled / reason. Card kind × ownership, the AC review statuses, the read-only
 * rows every Model with change operations carries, AI running, disposed, the group's final approval
 * job, an approval in flight and the reason order (D#1 §8.3 rule 6-1); then the display keys of the
 * check and Git states, the Git choice the dialog starts on, the line after an answer and the refusal
 * keys, with every key present in ko · en · ja.
 */
import { describe, expect, it } from 'vitest'
import {
  CHECK_GIT_STATUSES,
  CHECK_OPERATION_CLASS,
  CHECK_OPERATIONS,
  checkApprovalRunning,
  checkAvailability,
  checkDefaultChoice,
  checkFailureKey,
  checkGitErrorKey,
  checkGitLine,
  checkGitNotes,
  checkGitOffered,
  checkJobLine,
  checkOutcome,
  checkReviewKey,
  checkReviewStatus,
  type CheckAvailabilityItem,
  type CheckFailureKind,
  type CheckGitFacts,
  type CheckInput,
  type CheckOperation,
  type CheckOutcomeFacts,
} from '@/features/designer/inspector/features/check/checkModel'
import messages from '@/features/designer/inspector/features/check/messages'
import { INSPECTOR_CARD_KINDS, INSPECTOR_OWNERSHIPS, inspectorReadOnly } from '@/features/designer/inspector/inspectorTarget'

const AC = 'test2.default.0034.0006-AC'

const BASE: CheckInput = {
  cardKind: 'check',
  readOnly: null,
  docReviewStatus: 'pending_review',
  aiRunning: false,
  groupDisposed: false,
  approvalRunning: false,
  busy: false,
}

const input = (fields: Partial<CheckInput>): CheckInput => ({ ...BASE, ...fields })

/** Compact cell: '-' hidden, 'on' visible + enabled, or the reason key of a visible disabled one. */
function cell(item: CheckAvailabilityItem): string {
  if (!item.visible) {
    expect(item).toEqual({ visible: false, enabled: false, reason: null })
    return '-'
  }
  if (item.enabled) {
    expect(item.reason).toBeNull()
    return 'on'
  }
  expect(item.reason).not.toBeNull()
  return item.reason!
}

function row(fields: Partial<CheckInput>): Record<CheckOperation, string> {
  const availability = checkAvailability(input(fields))
  return Object.fromEntries(CHECK_OPERATIONS.map((op) => [op, cell(availability[op])])) as Record<CheckOperation, string>
}

const GIT: CheckGitFacts = {
  status: 'awaiting_choice',
  defaultAction: 'merge',
  choices: ['merge', 'merge_only', 'wait'],
  aheadCount: 2,
  behindCount: 0,
  mergeCommit: null,
  approvalPending: false,
  approvalInFlight: false,
  approvalJob: null,
}

const git = (fields: Partial<CheckGitFacts>): CheckGitFacts => ({ ...GIT, ...fields })

/** Deep key lookup in a messages tree. */
function has(tree: unknown, key: string): boolean {
  let node: unknown = tree
  for (const part of key.split('.')) {
    if (!node || typeof node !== 'object' || !(part in (node as object))) return false
    node = (node as Record<string, unknown>)[part]
  }
  return typeof node === 'string'
}

describe('checkModel — operations (flowgate.v02.0048 T#12)', () => {
  it('the final approval changes the server; the Git panel and ↗ 열기 only look (D#1 §8.3 rule 0)', () => {
    expect(CHECK_OPERATION_CLASS).toEqual({ approve: 'change', openGit: 'query', open: 'query' })
  })
})

describe('checkModel — card kind × ownership, AC under review', () => {
  const rows = INSPECTOR_CARD_KINDS.flatMap((cardKind) =>
    INSPECTOR_OWNERSHIPS.map((ownership) => {
      const readOnly = inspectorReadOnly(ownership, ownership === 'conflict' ? null : 'draft')
      const check = cardKind === 'check'
      const approve = check ? (readOnly === 'conflict' ? 'reason.read_only_conflict' : 'on') : '-'
      return { cardKind, ownership, readOnly, expected: { approve, openGit: check ? 'on' : '-', open: check ? 'on' : '-' } }
    }),
  )

  it('covers every card kind and ownership', () => {
    expect(rows).toHaveLength(INSPECTOR_CARD_KINDS.length * INSPECTOR_OWNERSHIPS.length)
  })

  it.each(rows)('$cardKind × $ownership', ({ cardKind, readOnly, expected }) => {
    expect(row({ cardKind, readOnly })).toEqual(expected)
  })
})

describe('checkModel — AC review status', () => {
  it.each([
    ['pending_review', 'on'],
    ['revised', 'on'],
    ['approved', '-'],
    ['wf_done', '-'],
    ['rejected', '-'],
    ['draft', '-'],
    [null, '-'],
  ] as const)('%s: final approval %s; the Git panel and ↗ 열기 always', (docReviewStatus, approve) => {
    expect(row({ docReviewStatus })).toEqual({ approve, openGit: 'on', open: 'on' })
  })
})

describe('checkModel — read only (D#1 §8.5 required rows)', () => {
  const view = (ownership: string, viewMode: string | null) => ({ readOnly: inspectorReadOnly(ownership, viewMode) })

  it.each([
    // 1. graph × revision: change disabled with the revision reason, queries usable
    ['graph × revision', view('graph', 'revision'), { approve: 'reason.read_only_revision', openGit: 'on', open: 'on' }],
    // 2. graph × draft: usable — only the view value differs from row 1
    ['graph × draft', view('graph', 'draft'), { approve: 'on', openGit: 'on', open: 'on' }],
    // 3. unassigned × revision / draft: as 1 / 2
    ['unassigned × revision', view('unassigned', 'revision'), { approve: 'reason.read_only_revision', openGit: 'on', open: 'on' }],
    ['unassigned × draft', view('unassigned', 'draft'), { approve: 'on', openGit: 'on', open: 'on' }],
    // 4. conflict × revision: the conflict reason wins
    ['conflict × revision', view('conflict', 'revision'), { approve: 'reason.read_only_conflict', openGit: 'on', open: 'on' }],
    ['v01 × null', view('v01', null), { approve: 'on', openGit: 'on', open: 'on' }],
  ])('%s', (_label, fields, expected) => {
    expect(row(fields)).toEqual(expected)
  })

  it.each([
    ['AI running', { aiRunning: true }],
    ['disposed', { groupDisposed: true }],
    ['approval running', { approvalRunning: true }],
    ['busy', { busy: true }],
  ])('5. revision view × %s: the revision reason', (_label, fields) => {
    expect(row({ readOnly: 'revision', ...fields }).approve).toBe('reason.read_only_revision')
  })
})

describe('checkModel — reason order (D#1 §8.3 rule 6-1)', () => {
  it.each([
    [{ readOnly: 'conflict' as const, groupDisposed: true, aiRunning: true, approvalRunning: true, busy: true }, 'reason.read_only_conflict'],
    [{ readOnly: 'revision' as const, groupDisposed: true, aiRunning: true, approvalRunning: true, busy: true }, 'reason.read_only_revision'],
    [{ groupDisposed: true, aiRunning: true, approvalRunning: true, busy: true }, 'reason.disposed'],
    [{ aiRunning: true, approvalRunning: true, busy: true }, 'reason.ai_running'],
    [{ approvalRunning: true, busy: true }, 'reason.approval_running'],
    [{ busy: true }, 'reason.busy'],
    [{}, 'on'],
  ])('%o -> %s', (fields, approve) => {
    expect(row(fields)).toEqual({ approve, openGit: 'on', open: 'on' })
  })

  it('the Git panel and ↗ 열기 stay usable whatever blocks the final approval', () => {
    const blocked = row({ readOnly: 'conflict', groupDisposed: true, aiRunning: true, approvalRunning: true, busy: true })
    expect([blocked.openGit, blocked.open]).toEqual(['on', 'on'])
  })
})

describe('checkModel — the review status read and its key', () => {
  it('the read document wins when it is the target; otherwise the Canvas one', () => {
    const target = { docId: AC, docReviewStatus: 'pending_review' }
    expect(checkReviewStatus(target, { docId: AC, docReviewStatus: 'approved' })).toBe('approved')
    expect(checkReviewStatus(target, { docId: 'other', docReviewStatus: 'approved' })).toBe('pending_review')
    expect(checkReviewStatus(target, null)).toBe('pending_review')
  })

  it.each([
    ['pending_review', 'review.pending'],
    ['revised', 'review.pending'],
    ['approved', 'review.approved'],
    ['wf_done', 'review.approved'],
    ['rejected', 'review.rejected'],
    ['draft', 'review.other'],
    [null, 'review.unknown'],
  ] as const)('%s -> %s', (status, key) => {
    expect(checkReviewKey(status)).toBe(key)
  })
})

describe('checkModel — Git reflection state', () => {
  it.each([
    ['none', 'git.status.none', 'muted'],
    ['awaiting_choice', 'git.status.awaiting_choice', 'warning'],
    ['waiting', 'git.status.waiting', 'warning'],
    ['merging', 'git.status.merging', 'muted'],
    ['conflict', 'git.status.conflict', 'danger'],
    ['merged', 'git.status.merged', 'success'],
    ['pushed', 'git.status.pushed', 'success'],
    ['discarded', 'git.status.discarded', 'muted'],
    ['archived', 'git.status.archived', 'muted'],
    ['stashed', 'git.status.stashed', 'muted'],
    ['reconciling', 'git.status.other', 'muted'],
  ])('%s -> %s (%s)', (status, key, tone) => {
    expect(checkGitLine(status)).toEqual({ key, status, tone })
  })

  it('every known status has its own wording', () => {
    for (const status of CHECK_GIT_STATUSES) expect(checkGitLine(status).key).toBe(`git.status.${status}`)
  })

  it('notes: a pending approval first, the merge commit of a merged slot, ahead / behind', () => {
    expect(checkGitNotes(git({ approvalPending: true, status: 'merged', mergeCommit: 'abcdef0123456789' }))).toEqual([
      { key: 'git.approval_pending', params: {} },
      { key: 'git.merge_commit', params: { commit: 'abcdef0123' } },
      { key: 'git.ahead_behind', params: { ahead: 2, behind: 0 } },
    ])
    expect(checkGitNotes(git({ status: 'none', aheadCount: null, behindCount: null }))).toEqual([])
    // A merge commit of an awaiting slot is not shown (it belongs to an older attempt).
    expect(checkGitNotes(git({ mergeCommit: 'abc' })).map((note) => note.key)).toEqual(['git.ahead_behind'])
  })

  it.each([
    ['awaiting with choices', git({}), true],
    ['waiting with choices', git({ status: 'waiting' }), true],
    ['awaiting without choices', git({ choices: [] }), false],
    ['merged', git({ status: 'merged', choices: [] }), false],
    ['conflict', git({ status: 'conflict', choices: [] }), false],
    ['none', git({ status: 'none', choices: [] }), false],
    ['not read', null, false],
  ])('the Git choice is asked: %s -> %s', (_label, state, offered) => {
    expect(checkGitOffered(state)).toBe(offered)
  })

  it.each([
    // project default -> the start without its push (the v0.1 confirm dialog)
    ['merge', ['merge', 'merge_only', 'wait'], 'merge_only'],
    ['merge_only', ['merge', 'merge_only', 'wait'], 'merge_only'],
    ['wait', ['merge', 'merge_only', 'wait'], 'wait'],
    ['push', ['merge', 'merge_only', 'wait'], 'wait'],
    ['commit_push', ['merge', 'merge_only', 'wait'], 'wait'],
    ['commit_only', ['merge', 'merge_only', 'wait'], 'wait'],
    [null, ['merge', 'merge_only', 'wait'], 'wait'],
    ['merge', ['merge', 'wait'], 'wait'],
    ['merge', ['merge'], 'merge'],
    ['merge', [], null],
  ] as const)('default %s of %o starts on %s', (defaultAction, choices, start) => {
    expect(checkDefaultChoice(git({ defaultAction, choices: [...choices] }))).toBe(start)
  })
})

describe('checkModel — the final approval job', () => {
  const job = (fields: Partial<NonNullable<CheckGitFacts['approvalJob']>>) => ({ jobId: 'job_7', status: 'blocked', stage: 'freeze_wait', docId: AC, ...fields })

  it.each([
    ['nothing', git({}), false],
    ['lock held', git({ approvalInFlight: true }), true],
    ['unknown lock, no job', git({ approvalInFlight: null }), false],
    ['this AC waits', git({ approvalJob: job({}) }), true],
    ['a job of no document', git({ approvalJob: job({ docId: null }) }), true],
    ['another AC', git({ approvalJob: job({ docId: 'other-AC' }) }), false],
    ['recovery required: the person retries', git({ approvalInFlight: true, approvalJob: job({ status: 'recovery_required', stage: 'recovery_required' }) }), false],
    ['not read', null, false],
  ])('%s -> running %s', (_label, state, running) => {
    expect(checkApprovalRunning(state, AC)).toBe(running)
  })

  it('the job line: queued with its stage, recovery, none for another AC', () => {
    expect(checkJobLine(git({ approvalJob: job({}) }), AC)).toEqual({ key: 'job.queued', params: { job: 'job_7', stage: 'freeze_wait' } })
    expect(checkJobLine(git({ approvalJob: job({ stage: null, status: 'recovery_required' }) }), AC)).toEqual({
      key: 'job.recovery',
      params: { job: 'job_7', stage: 'recovery_required' },
    })
    expect(checkJobLine(git({ approvalJob: job({ docId: 'other-AC' }) }), AC)).toBeNull()
    expect(checkJobLine(null, AC)).toBeNull()
  })
})

describe('checkModel — the line after a final approval', () => {
  const answer = (fields: Partial<CheckOutcomeFacts>, gitFields: Partial<NonNullable<CheckOutcomeFacts['git']>> | null = null): CheckOutcomeFacts => ({
    approved: true,
    stage: null,
    jobId: null,
    git: gitFields === null ? null : { ok: true, quiet: false, status: null, mergeCommit: null, pushed: null, conflictFiles: 0, errorCode: null, ...gitFields },
    ...fields,
  })

  it.each([
    ['plain approval', answer({}), 'outcome.approved', 'success', false, {}],
    ['quiet (nothing to reflect)', answer({}, { quiet: true }), 'outcome.approved', 'success', false, {}],
    ['merged + pushed', answer({}, { status: 'merged', mergeCommit: 'abcdef0123456789', pushed: true }), 'outcome.merged', 'success', false, { commit: 'abcdef0123' }],
    ['merged, not pushed', answer({}, { status: 'merged', mergeCommit: 'abcdef0123456789', pushed: false }), 'outcome.merged_local', 'success', false, { commit: 'abcdef0123' }],
    ['pushed', answer({}, { status: 'pushed' }), 'outcome.pushed', 'success', false, {}],
    ['waiting', answer({}, { status: 'waiting' }), 'outcome.waiting', 'muted', false, {}],
    ['archived', answer({}, { status: 'archived' }), 'outcome.archived', 'success', false, {}],
    ['Git failed, approval stands', answer({}, { ok: false, errorCode: 'base_dirty' }), 'outcome.git_failed', 'warning', true, {}],
    ['approved with a conflict result', answer({}, { status: 'conflict', conflictFiles: 3 }), 'outcome.conflict', 'warning', true, { n: 3 }],
    ['queued', answer({ approved: false, stage: 'queued', jobId: 'job_2' }, {}), 'outcome.queued', 'muted', false, { job: 'job_2' }],
    ['recovery required', answer({ approved: false, stage: 'recovery_required', jobId: 'job_9' }), 'outcome.recovery', 'warning', true, { job: 'job_9' }],
    ['handed to a conflict review', answer({ approved: false, stage: 'git_finalize' }, { status: 'conflict', conflictFiles: 2 }), 'outcome.conflict', 'warning', true, { n: 2 }],
    ['not approved, no reason', answer({ approved: false }), 'outcome.not_approved', 'warning', false, {}],
    ['not approved, Git failed', answer({ approved: false }, { ok: false }), 'outcome.not_approved', 'warning', true, {}],
  ] as const)('%s -> %s', (_label, result, key, tone, toGit, params) => {
    expect(checkOutcome(result)).toEqual({ key, tone, toGit, params })
  })
})

describe('checkModel — refusal and read failure keys', () => {
  const kinds: CheckFailureKind[] = ['running', 'disposed', 'conflict', 'forbidden', 'not_found', 'invalid', 'network', 'other', 'git_busy', 'stale_choice', 'precheck']

  it.each(kinds)('%s -> failure.%s', (kind) => {
    expect(checkFailureKey(kind)).toBe(`failure.${kind}`)
  })

  it.each([
    ['forbidden', 'git.read_forbidden'],
    ['not_found', 'git.read_failed'],
    ['network', 'git.read_failed'],
    ['other', 'git.read_failed'],
  ])('Git read %s -> %s', (kind, key) => {
    expect(checkGitErrorKey(kind)).toBe(key)
  })

  it('every key the Model can answer exists in ko · en · ja', () => {
    const keys = [
      ...['read_only_conflict', 'read_only_revision', 'disposed', 'ai_running', 'approval_running', 'busy'].map((reason) => `reason.${reason}`),
      ...kinds.map(checkFailureKey),
      ...['pending_review', 'approved', 'rejected', 'draft', null].map((status) => checkReviewKey(status)),
      ...[...CHECK_GIT_STATUSES, 'x'].map((status) => checkGitLine(status).key),
      'git.approval_pending',
      'git.merge_commit',
      'git.ahead_behind',
      'git.read_forbidden',
      'git.read_failed',
      'job.queued',
      'job.recovery',
      ...['approved', 'merged', 'merged_local', 'pushed', 'waiting', 'archived', 'git_failed', 'conflict', 'queued', 'recovery', 'not_approved'].map((key) => `outcome.${key}`),
      ...['merge', 'merge_only', 'wait'].flatMap((choice) => [`choice.${choice}`, `choice_desc.${choice}`]),
    ]
    for (const locale of ['ko', 'en', 'ja'] as const) {
      for (const key of keys) expect(has(messages[locale], key), `${locale}.${key}`).toBe(true)
    }
  })
})
