/**
 * flowgate.v02.0048 T#6 — actionsModel as tables (D#1 §8.5): rows are input combinations, columns
 * the operations' visible / enabled / reason. Card kind × ownership, card kind × review status,
 * the read-only rows every Model with change operations carries, AI running, disposed, an action in
 * flight and the reason order (D#1 §8.3 rule 6-1); then the review status the rules read and the
 * refusal keys.
 * flowgate.v02.0056 F2-1: the header's document approve / reject are gone (a card is approved with its own
 * [승인]; the gate keeps ✓ 확인 · ✕ 반려) — the tables hold the v0.1 run / rework only, and a row pins that no
 * approve / reject operation, string or TR commit line is left.
 */
import { describe, expect, it } from 'vitest'
import * as actionsModel from '@/features/designer/inspector/features/actions/actionsModel'
import {
  ACTIONS_OPERATION_CLASS,
  ACTIONS_OPERATIONS,
  actionsAvailability,
  actionsFailureKey,
  actionsReviewStatus,
  type ActionsAvailabilityItem,
  type ActionsInput,
  type ActionsOperation,
} from '@/features/designer/inspector/features/actions/actionsModel'
import { INSPECTOR_CARD_KINDS, INSPECTOR_OWNERSHIPS, inspectorReadOnly, type InspectorCardKind } from '@/features/designer/inspector/inspectorTarget'
import messages from '@/features/designer/inspector/features/actions/messages'
import registration from '@/features/designer/inspector/features/actions/register'

const BASE: ActionsInput = {
  cardKind: 'task',
  ownership: 'v01',
  readOnly: null,
  docReviewStatus: 'pending_review',
  aiRunning: false,
  groupDisposed: false,
  busy: false,
}

const input = (fields: Partial<ActionsInput>): ActionsInput => ({ ...BASE, ...fields })

/** Compact cell: '-' hidden, 'on' visible + enabled, or the reason key of a visible disabled one. */
function cell(item: ActionsAvailabilityItem): string {
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

function row(fields: Partial<ActionsInput>): Record<ActionsOperation, string> {
  const availability = actionsAvailability(input(fields))
  return Object.fromEntries(ACTIONS_OPERATIONS.map((op) => [op, cell(availability[op])])) as Record<ActionsOperation, string>
}

const RUN_CARDS: readonly InspectorCardKind[] = ['research', 'task', 'design']

describe('actionsModel — no approve / reject in the header (flowgate.v02.0056 F2-1)', () => {
  it('the operations are the v0.1 run and rework only', () => {
    expect(ACTIONS_OPERATIONS).toEqual(['run', 'rework'])
    expect(Object.keys(actionsAvailability(BASE)).sort()).toEqual(['rework', 'run'])
    expect(Object.keys(actionsModel)).not.toContain('actionsTrCommitLine')
    expect(Object.keys(actionsModel)).not.toContain('ACTIONS_REVIEW_CARDS')
  })

  it('no approve / reject wording, reason dialog or TR commit line is left in any locale', () => {
    for (const locale of ['ko', 'en', 'ja'] as const) {
      const keys = Object.keys(messages[locale])
      for (const gone of ['approve', 'reject', 'reject_dialog', 'tr_commit']) expect(keys, `${locale} ${gone}`).not.toContain(gone)
    }
  })

  it('the header actions apply to the v0.1 work cards of a v0.1 Flow only (the run / rework they offer)', () => {
    expect(registration.cardKinds).toEqual(['research', 'task', 'design'])
    expect(registration.ownerships).toEqual(['v01'])
    expect(registration.headerAction).toBeTruthy()
  })
})

describe('actionsModel — card kind × ownership, document under review (flowgate.v02.0048 T#6)', () => {
  const rows = INSPECTOR_CARD_KINDS.flatMap((cardKind) =>
    INSPECTOR_OWNERSHIPS.map((ownership) => {
      const readOnly = inspectorReadOnly(ownership, ownership === 'conflict' ? null : 'draft')
      const run = RUN_CARDS.includes(cardKind) && ownership === 'v01' ? 'on' : '-'
      return { cardKind, ownership, readOnly, expected: { run, rework: '-' } }
    }),
  )

  it('covers every card kind and ownership', () => {
    expect(rows).toHaveLength(INSPECTOR_CARD_KINDS.length * INSPECTOR_OWNERSHIPS.length)
  })

  it.each(rows)('$cardKind × $ownership', ({ cardKind, ownership, readOnly, expected }) => {
    expect(row({ cardKind, ownership, readOnly })).toEqual(expected)
  })
})

describe('actionsModel — card kind × review status on a v0.1 Flow', () => {
  it.each([
    // status          run          rework
    ['pending_review', { run: 'on', rework: '-' }],
    ['revised', { run: 'on', rework: '-' }],
    ['approved', { run: '-', rework: '-' }],
    ['wf_done', { run: '-', rework: '-' }],
    ['rejected', { run: '-', rework: 'on' }],
    ['wf_in_progress', { run: 'on', rework: '-' }],
    [null, { run: 'on', rework: '-' }],
  ] as const)('task × %s', (docReviewStatus, expected) => {
    expect(row({ docReviewStatus })).toEqual(expected)
  })

  it.each(INSPECTOR_CARD_KINDS)('%s × rejected: rework on work cards only', (cardKind) => {
    expect(row({ cardKind, docReviewStatus: 'rejected' }).rework).toBe(RUN_CARDS.includes(cardKind) ? 'on' : '-')
  })

  it.each(['graph', 'unassigned', 'conflict'])('no run or rework outside v01 (%s)', (ownership) => {
    expect(row({ ownership, docReviewStatus: 'pending_review' }).run).toBe('-')
    expect(row({ ownership, docReviewStatus: 'rejected' }).rework).toBe('-')
  })

  it('a requirement (R/B) shows no ▶ run on v01 — AI delegation is not offered', () => {
    expect(row({ cardKind: 'requirement', docReviewStatus: null })).toEqual({ run: '-', rework: '-' })
    expect(row({ cardKind: 'requirement', docReviewStatus: 'rejected' }).rework).toBe('-')
  })

  it('test (TS/TSR/V), plan (WP), check (AC) and other cards have no header action here', () => {
    for (const cardKind of ['test', 'plan', 'check', 'other'] as const) {
      for (const docReviewStatus of ['pending_review', 'revised', 'rejected', null]) {
        expect(row({ cardKind, docReviewStatus })).toEqual({ run: '-', rework: '-' })
      }
    }
  })
})

describe('actionsModel — read-only rows (D#1 §8.5)', () => {
  it.each([
    ['1 graph × revision', { ownership: 'graph', readOnly: inspectorReadOnly('graph', 'revision') }, { run: '-', rework: '-' }],
    ['2 graph × draft', { ownership: 'graph', readOnly: inspectorReadOnly('graph', 'draft') }, { run: '-', rework: '-' }],
    ['3 unassigned × revision', { ownership: 'unassigned', readOnly: inspectorReadOnly('unassigned', 'revision') }, { run: '-', rework: '-' }],
    ['4 conflict × revision', { ownership: 'conflict', readOnly: inspectorReadOnly('conflict', 'revision') }, { run: '-', rework: '-' }],
    ['v01 × revision', { ownership: 'v01', readOnly: 'revision' as const }, { run: 'reason.read_only_revision', rework: '-' }],
    ['v01 × conflict (run keeps its v01 visibility, disabled)', { ownership: 'v01', readOnly: 'conflict' as const }, { run: 'reason.read_only_conflict', rework: '-' }],
  ] as const)('%s', (_label, fields, expected) => {
    expect(row(fields)).toEqual(expected)
  })

  it('every operation is a change operation (D#1 §8.3 rule 0)', () => {
    expect(ACTIONS_OPERATIONS.map((op) => ACTIONS_OPERATION_CLASS[op])).toEqual(['change', 'change'])
  })
})

describe('actionsModel — AI running, disposed, in flight and the reason order (D#1 §8.3 rules 6 / 6-1)', () => {
  it.each([
    ['AI running', { aiRunning: true }, 'reason.ai_running'],
    ['disposed', { groupDisposed: true }, 'reason.disposed'],
    ['an action in flight', { busy: true }, 'reason.busy'],
    ['disposed before AI running', { groupDisposed: true, aiRunning: true }, 'reason.disposed'],
    ['AI running before in flight', { aiRunning: true, busy: true }, 'reason.ai_running'],
    ['conflict before everything', { readOnly: 'conflict' as const, groupDisposed: true, aiRunning: true, busy: true }, 'reason.read_only_conflict'],
    ['revision before disposed', { readOnly: 'revision' as const, groupDisposed: true, aiRunning: true }, 'reason.read_only_revision'],
  ] as const)('%s', (_label, fields, reason) => {
    expect(row(fields)).toEqual({ run: reason, rework: '-' })
    expect(row({ ...fields, docReviewStatus: 'rejected' }).rework).toBe(reason)
  })

  it('a hidden operation stays hidden whatever blocks the others', () => {
    expect(row({ docReviewStatus: 'approved', aiRunning: true, groupDisposed: true })).toEqual({ run: '-', rework: '-' })
  })
})

describe('actionsModel — status source and refusals', () => {
  const target = { docId: 'd1', docReviewStatus: 'pending_review' }

  it('reads the fetched status of the same document, else the Canvas one', () => {
    expect(actionsReviewStatus(target, null)).toBe('pending_review')
    expect(actionsReviewStatus(target, { docId: 'd1', docReviewStatus: 'rejected' })).toBe('rejected')
    expect(actionsReviewStatus(target, { docId: 'd0', docReviewStatus: 'rejected' })).toBe('pending_review')
    expect(actionsReviewStatus(target, { docId: 'd1', docReviewStatus: null })).toBeNull()
  })

  it.each(['running', 'disposed', 'conflict', 'forbidden', 'not_found', 'invalid', 'network', 'other'] as const)('refusal %s has a message in every locale', (kind) => {
    const key = actionsFailureKey(kind)
    expect(key).toBe(`failure.${kind}`)
    for (const locale of ['ko', 'en', 'ja'] as const) expect((messages[locale].failure as Record<string, string>)[kind]).toBeTruthy()
  })

  it('every disabled reason has a message in every locale', () => {
    const reasons = new Set<string>()
    for (const fields of [{ readOnly: 'conflict' as const }, { readOnly: 'revision' as const }, { groupDisposed: true }, { aiRunning: true }, { busy: true }]) {
      reasons.add(actionsAvailability(input(fields)).run.reason!)
    }
    for (const reason of reasons) {
      const name = reason.replace(/^reason\./, '')
      for (const locale of ['ko', 'en', 'ja'] as const) expect((messages[locale].reason as Record<string, string>)[name]).toBeTruthy()
    }
    expect([...reasons].sort()).toEqual(['reason.ai_running', 'reason.busy', 'reason.disposed', 'reason.read_only_conflict', 'reason.read_only_revision'])
  })
})
