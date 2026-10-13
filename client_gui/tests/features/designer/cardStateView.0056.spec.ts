/**
 * flowgate.v02.0056 F2-1 — cardStateView as tables (O1 0005-TR §4-2, O2 0007-TR §4-2): the three card states
 * by the node run alone — 작업 중 (open: 지시서 작성 중 / 결과 작성 중; review while an AI still works), 승인 대기
 * (awaiting_approval), 승인됨 (done) — a rejected R/B root that never offers [승인], and the [승인] refusals grouped
 * by the server's code (O1 4-1). Every key the rules produce has a ko / en / ja string.
 */
import { describe, expect, it } from 'vitest'
import {
  CARD_APPROVAL_CODES,
  CARD_STATE_KEYS,
  cardApprovalFailureView,
  cardStateView,
  type CardRunFacts,
} from '@/features/designer/center/graph/cardStateView'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'

const run = (status: string | null, fields: Partial<CardRunFacts> = {}): CardRunFacts => ({ status, ...fields })

/** Compact row: key · phase · tone · [승인]. */
const row = (facts: CardRunFacts | null) => {
  const view = cardStateView(facts)
  return view ? [view.key, view.phase, view.tone, view.approvable ? '승인' : '-'] : null
}

describe('cardStateView — the three card states (flowgate.v02.0056 F2-1)', () => {
  it.each([
    // node run                                                   key                    phase       tone       [승인]
    ['open · instruction', run('open', { stage: 'instruction' }), ['writing_instruction', 'working', 'info', '-']],
    ['open · result', run('open', { stage: 'result' }), ['writing_result', 'working', 'info', '-']],
    ['open · no stage (a run before O2, a person’s card)', run('open', { stage: null }), ['working', 'working', 'info', '-']],
    ['review · an AI still works (its run, the review loop)', run('review', { awaitingApproval: false }), ['working', 'working', 'info', '-']],
    ['review · no awaiting_approval sent', run('review'), ['working', 'working', 'info', '-']],
    ['review · awaiting approval', run('review', { awaitingApproval: true, stage: 'result' }), ['awaiting_approval', 'awaiting', 'warning', '승인']],
    ['done', run('done', { awaitingApproval: false }), ['approved', 'approved', 'success', '-']],
    ['rejected R/B root (O1 rev 2: awaiting_approval false)', run('rejected', { awaitingApproval: false }), ['rejected', 'other', 'danger', '-']],
    ['pending', run('pending'), ['pending', 'other', 'neutral', '-']],
    ['closed', run('closed'), ['closed', 'other', 'neutral', '-']],
  ] as const)('%s', (_label, facts, expected) => {
    expect(row(facts)).toEqual(expected)
  })

  it('a done card is 승인됨 whatever else the run says — a [다음 단계] stop is not a card state', () => {
    // Approved while the run was stopped: the node is done, only its successors wait (the run band's line).
    expect(row(run('done', { awaitingApproval: true }))).toEqual(['approved', 'approved', 'success', '-'])
    expect(row(run('done', { stage: 'result' }))).toEqual(['approved', 'approved', 'success', '-'])
  })

  it('a rejected root never offers [승인], even if the server said it waits', () => {
    expect(cardStateView(run('rejected', { awaitingApproval: true }))!.approvable).toBe(false)
  })

  it('no run or an unknown status: no card state (the Canvas keeps its own state)', () => {
    expect(cardStateView(null)).toBeNull()
    expect(cardStateView(undefined)).toBeNull()
    expect(cardStateView(run(null))).toBeNull()
    expect(cardStateView(run('waiting_review'))).toBeNull()
  })

  it('every state has a label in ko / en / ja (gui.designer.center.card_states)', () => {
    for (const messages of [ko, en, ja]) {
      expect(Object.keys(messages.designer.center.card_states).sort()).toEqual([...CARD_STATE_KEYS].sort())
    }
    expect(ko.designer.center.card_states).toMatchObject({ writing_instruction: '지시서 작성 중', writing_result: '결과 작성 중', working: '작업 중', awaiting_approval: '승인 대기', approved: '승인됨' })
    expect(cardStateView(run('review', { awaitingApproval: true }))!.labelKey).toBe('gui.designer.center.card_states.awaiting_approval')
  })
})

describe('cardApprovalFailureView — a refused [승인] by its code (O1 4-1)', () => {
  it.each([
    // code                          status  kind         reread  keep button
    ['result_changed', 409, 'changed', true, false],
    ['group_ai_running', 409, 'busy', false, true],
    ['card_approval_busy', 409, 'busy', false, true],
    ['ai_running', 409, 'busy', false, true],
    ['already_approved', 409, 'stale', true, false],
    ['node_not_in_review', 409, 'stale', true, false],
    ['no_result', 409, 'stale', true, false],
    ['node_not_started', 409, 'stale', true, false],
    ['document_approval_refused', 409, 'refused', true, false],
    ['approval_forbidden', 403, 'forbidden', false, false],
    ['flow_access_denied', 403, 'forbidden', false, false],
    ['run_not_active', 409, 'inactive', true, false],
    ['node_is_gate', 409, 'inactive', true, false],
    ['flow_disposed', 409, 'inactive', true, false],
    [null, 403, 'forbidden', false, false],
    [null, 500, 'other', false, true],
    ['something_new', 409, 'other', false, true],
  ] as const)('%s (%s): %s', (code, status, kind, reread, keepButton) => {
    expect(cardApprovalFailureView(code, status)).toMatchObject({ kind, reread, keepButton })
  })

  it('no answer is `network` (the button stays)', () => {
    expect(cardApprovalFailureView(null, null, true)).toMatchObject({ kind: 'network', keepButton: true, labelKey: 'gui.designer.center.approval.failure.network' })
  })

  it('a code with a sentence of its own uses it; the rest use their kind’s', () => {
    expect(cardApprovalFailureView('result_changed', 409).labelKey).toBe('gui.designer.center.approval.failure.result_changed')
    expect(cardApprovalFailureView('flow_access_denied', 403).labelKey).toBe('gui.designer.center.approval.failure.forbidden')
    expect(cardApprovalFailureView('run_not_active', 409).labelKey).toBe('gui.designer.center.approval.failure.inactive')
    expect(cardApprovalFailureView('weird', 409).labelKey).toBe('gui.designer.center.approval.failure.other')
  })

  it('every refusal sentence and the [승인] wording exist in ko / en / ja', () => {
    const keys = [...CARD_APPROVAL_CODES, 'forbidden', 'inactive', 'network', 'other']
    for (const messages of [ko, en, ja]) {
      const approval = messages.designer.center.approval as Record<string, unknown>
      for (const key of ['approve', 'hint', 'done', 'refused_document', 'refused_reason', 'approved_documents']) expect(typeof approval[key], key).toBe('string')
      expect(Object.keys(approval.failure as Record<string, string>).sort()).toEqual([...keys].sort())
    }
  })
})
