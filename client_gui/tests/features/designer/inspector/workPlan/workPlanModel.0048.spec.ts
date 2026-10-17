/**
 * flowgate.v02.0048 T#13 — the WP summary rules as tables (D#1 §8.5, §10.1 WP 요약 column, §10.2 T#13 row):
 * the Step summary (one row per card: pairs joined, providers, review count, server-assembled, started), the
 * progress tally, where the plan is edited, the operation classes (queries only), availability across card
 * kind × ownership × view with the read-only row of §8.5 for a Model without change operations, the keys of
 * a failed read, and every key with its ko / en / ja wording.
 */
import { describe, expect, it } from 'vitest'
import { toWorkPlanView } from '@/features/document'
import workPlanMessages from '@/features/designer/inspector/features/workPlan/messages'
import {
  WORK_PLAN_OPERATION_CLASS,
  workPlanAvailability,
  workPlanCards,
  workPlanErrorKey,
  workPlanProgress,
} from '@/features/designer/inspector/features/workPlan/workPlanModel'
import { INSPECTOR_CARD_KINDS, inspectorReadOnly } from '@/features/designer/inspector/inspectorTarget'
import { pairRows, stepRow, workPlanDocument } from './workPlanFixtures'

const plan = (fields: Record<string, unknown> = {}, started?: readonly string[]) => toWorkPlanView(workPlanDocument(fields, started))

// --- Step summary ----------------------------------------------------------------------------------

describe('Step summary (flowgate.v02.0048 T#13)', () => {
  it('one row per card in plan order: a pair is one card, providers by current name, review count, started', () => {
    expect(workPlanCards(plan({}, ['D#1', 'T#1']))).toEqual([
      { cardId: 'D#1', keys: ['D#1'], type: 'D', providers: ['Claude'], reviewCount: 0, locked: false, started: true },
      { cardId: 'D#2', keys: ['D#2'], type: 'D', providers: [], reviewCount: 0, locked: false, started: false },
      { cardId: 'T#1', keys: ['T#1', 'TR#1'], type: 'T', providers: ['Codex'], reviewCount: 1, locked: false, started: true },
      { cardId: 'T#2', keys: ['T#2', 'TR#2'], type: 'T', providers: ['Claude'], reviewCount: 0, locked: false, started: false },
    ])
  })

  it.each([
    ['different providers on a pair are both listed', pairRows('N', 'NR', 1).map((row, i) => ({ ...row, provider_id: i ? 'codex' : 'claude' })), ['Claude', 'Codex'], 0, false],
    ['the review count of the Step that asks for one', [pairRows('T', 'TR', 1)[0], { ...pairRows('T', 'TR', 1)[1], review_count: 2 }], [], 2, false],
    ['a server-assembled card', [stepRow('AC', 1, { locked: true, locked_reason: 'server_assembled', origin: 'system' })], [], 0, true],
    ['a pair with one assembled Step is not assembled', [pairRows('T', 'TR', 1)[0], { ...pairRows('T', 'TR', 1)[1], locked: true }], [], 0, false],
  ])('%s', (_name, steps, providers, reviewCount, locked) => {
    const [card] = workPlanCards(plan({ steps }, []))
    expect(card).toMatchObject({ providers, reviewCount, locked, started: false })
  })

  it('a card counts as started when any of its Steps did (the server marks by card, a half-marked pair still started)', () => {
    const view = plan({ steps: pairRows('T', 'TR', 1) }, [])
    const halfStarted = { ...view, steps: [{ ...view.steps[0], started: true }, view.steps[1]] }
    expect(workPlanCards(halfStarted)[0].started).toBe(true)
    expect(workPlanProgress(halfStarted)).toEqual({ cards: 1, started: 1, steps: 2 })
  })

  it('cards keep the order of their first Step, also when a result Step comes later', () => {
    const [t1, tr1] = pairRows('T', 'TR', 1)
    const cards = workPlanCards(plan({ steps: [t1, stepRow('D', 1), tr1] }, []))
    expect(cards.map((card) => card.keys)).toEqual([['T#1', 'TR#1'], ['D#1']])
  })

  it('no view or no Steps: no rows', () => {
    expect(workPlanCards(null)).toEqual([])
    expect(workPlanCards(plan({ steps: [] }))).toEqual([])
  })
})

// --- Progress ----------------------------------------------------------------------------------------

describe('progress (flowgate.v02.0048 T#13)', () => {
  it.each<[string, readonly string[], number]>([
    ['nothing started', [], 0],
    ['one single card', ['D#1'], 1],
    ['a pair counts once', ['D#1', 'T#1'], 2],
    ['every card', ['D#1', 'D#2', 'T#1', 'T#2'], 4],
  ])('%s', (_name, started, count) => {
    expect(workPlanProgress(plan({}, started))).toEqual({ cards: 4, started: count, steps: 6 })
  })

  it('no view: zero', () => {
    expect(workPlanProgress(null)).toEqual({ cards: 0, started: 0, steps: 0 })
  })
})

// --- Operations and availability ----------------------------------------------------------------------

describe('operations and availability (flowgate.v02.0048 T#13, D#1 §8.3 / §8.5)', () => {
  it('both operations are queries: the section changes nothing', () => {
    expect(WORK_PLAN_OPERATION_CLASS).toEqual({ read: 'query', open: 'query' })
  })

  const OPEN = { visible: true, enabled: true, reason: null }

  // Card kind × ownership: the registration decides the card (plan only); the Model never disables a query.
  const ownerships = ['v01', 'graph', 'unassigned', 'conflict'] as const
  const views = ['revision', 'draft', null] as const
  it.each(INSPECTOR_CARD_KINDS.flatMap((kind) => ownerships.map((ownership) => [kind, ownership] as const)))(
    '%s card × %s Flow: read and ↗ 열기 open',
    (_kind, ownership) => {
      const readOnly = inspectorReadOnly(ownership, ownership === 'graph' ? 'draft' : null)
      expect(workPlanAvailability({ readOnly, hasDocument: true })).toEqual({ read: OPEN, open: OPEN })
    },
  )

  // §8.5: a Model with no change operation keeps one row where every operation stays enabled in a read-only view.
  it.each(ownerships.flatMap((ownership) => views.map((view) => [ownership, view] as const)))(
    'read-only rows: %s × view %s — every operation enabled, no reason',
    (ownership, view) => {
      const readOnly = inspectorReadOnly(ownership, view)
      expect(workPlanAvailability({ readOnly, hasDocument: true })).toEqual({ read: OPEN, open: OPEN })
    },
  )

  it('graph × revision (readOnly = revision) and conflict: still open', () => {
    expect(inspectorReadOnly('graph', 'revision')).toBe('revision')
    expect(workPlanAvailability({ readOnly: 'revision', hasDocument: true }).open).toEqual(OPEN)
    expect(workPlanAvailability({ readOnly: 'conflict', hasDocument: true }).open).toEqual(OPEN)
  })

  it('no document: nothing is drawn', () => {
    const hidden = { visible: false, enabled: false, reason: null }
    expect(workPlanAvailability({ readOnly: null, hasDocument: false })).toEqual({ read: hidden, open: hidden })
  })
})

// --- Keys --------------------------------------------------------------------------------------------

describe('message keys (flowgate.v02.0048 T#13)', () => {
  it.each([
    ['unreadable', 'error.unreadable'],
    ['invalid', 'error.invalid'],
    ['forbidden', 'error.forbidden'],
    ['not_found', 'error.not_found'],
    ['network', 'error.network'],
    ['other', 'error.other'],
    ['conflict', 'error.other'],
  ])('a failed read of kind %s -> %s', (kind, key) => {
    expect(workPlanErrorKey(kind)).toBe(key)
  })

  const lookup = (messages: Record<string, unknown>, key: string) =>
    key.split('.').reduce<unknown>((node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined), messages)

  const keys = [
    'title',
    // flowgate.v02.0056 F1: the details' keys (rev, totals, assignment, per-card rows, open_hint, edit.*) are gone.
    'progress',
    'no_cards',
    'unassigned',
    'error.unreadable',
    'error.invalid',
    'error.forbidden',
    'error.not_found',
    'error.network',
    'error.other',
  ]

  it.each(['ko', 'en', 'ja'] as const)('every key the rules and the section use has %s wording', (locale) => {
    const missing = keys.filter((key) => typeof lookup(workPlanMessages[locale] as Record<string, unknown>, key) !== 'string')
    expect(missing).toEqual([])
  })

  // flowgate.v02.0051 stage 2 (B14 as turn 7 reset it): an undecided feature may be worded and drawn inactive;
  // the deploy strings exist with the reason they are inactive, and nothing names a replan or a memo.
  it.each(['ko', 'en', 'ja'] as const)('%s wording: ⇉ 전개 / 재전개 are worded with their inactive reason; no replan, delegation or memo wording', (locale) => {
    const messages = workPlanMessages[locale] as Record<string, unknown>
    for (const key of ['deploy.deploy', 'deploy.redeploy', 'deploy.unconnected', 'slot.plan', 'slot.deploy', 'slot_empty.deploy', 'author.human']) {
      expect(typeof lookup(messages, key), `${locale} ${key}`).toBe('string')
    }
    const words = JSON.stringify(messages)
    for (const word of ['재계획', '위임', '메모', 'AI 전달', 'replan', 'delegat', '再計画']) expect(words).not.toContain(word)
  })
})
