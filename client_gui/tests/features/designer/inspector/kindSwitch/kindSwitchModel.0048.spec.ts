/**
 * flowgate.v02.0048 T#10 — the kind switch's availability as one table (D#1 §8.5, D#2 §3.4): card kind ×
 * document type × ownership (4 + none) × read only (the §8.5 required rows) × AI running × disposed ×
 * a switch in flight -> visible / enabled / reason, the reason order of D#1 §8.3 rule 6-1, the kind a
 * root switches to, the refusal keys and which refusals read the Canvas again. Every key the rules
 * produce has a string in ko · en · ja.
 */
import { describe, expect, it } from 'vitest'
import messages from '@/features/designer/inspector/features/kindSwitch/messages'
import {
  KIND_SWITCH_OPERATIONS,
  KIND_SWITCH_OPERATION_CLASS,
  kindSwitchAvailability,
  kindSwitchFailureKey,
  kindSwitchRefreshesFlow,
  kindSwitchTarget,
  type KindSwitchFailureKind,
  type KindSwitchInput,
} from '@/features/designer/inspector/features/kindSwitch/kindSwitchModel'
import { INSPECTOR_CARD_DOC_TYPES, INSPECTOR_CARD_KINDS, type InspectorCardKind } from '@/features/designer/inspector/inspectorTarget'
import { SELECTIONS, targetOf } from '../support/inspectorSupport'

const BASE: KindSwitchInput = {
  cardKind: 'requirement',
  docType: 'R',
  hasDocument: true,
  ownership: 'unassigned',
  readOnly: null,
  aiRunning: false,
  groupDisposed: false,
  busy: false,
}

const at = (fields: Partial<KindSwitchInput>) => kindSwitchAvailability({ ...BASE, ...fields }).switch

const HIDDEN = { visible: false, enabled: false, reason: null }
const ON = { visible: true, enabled: true, reason: null }
const OFF = (reason: string) => ({ visible: true, enabled: false, reason })

describe('the switch is a change operation', () => {
  it('one operation, classed as change (D#1 §8.3 rule 0)', () => {
    expect(KIND_SWITCH_OPERATIONS).toEqual(['switch'])
    expect(KIND_SWITCH_OPERATION_CLASS).toEqual({ switch: 'change' })
  })
})

describe('shown on an unassigned requirement / bug root only (D#2 §3.4)', () => {
  const ownershipRows: readonly [string, string | null, string, object][] = [
    ['unassigned R', 'unassigned', 'R', ON],
    ['unassigned B', 'unassigned', 'B', ON],
    ['v01 R (a sequence: the converter always refuses, D#2 E1)', 'v01', 'R', HIDDEN],
    ['v01 B', 'v01', 'B', HIDDEN],
    ['graph R (the contact refuses, D#2 E2)', 'graph', 'R', HIDDEN],
    ['graph B', 'graph', 'B', HIDDEN],
    ['conflict R', 'conflict', 'R', HIDDEN],
    ['no ownership', null, 'R', HIDDEN],
    ['an unknown ownership key', 'future_engine', 'R', HIDDEN],
  ]
  it.each(ownershipRows)('%s', (_label, ownership, docType, expected) => {
    expect(at({ ownership, docType })).toEqual(expected)
  })

  const cardRows = INSPECTOR_CARD_KINDS.filter((kind) => kind !== 'requirement').flatMap((cardKind) => {
    const types = cardKind === 'other' ? ['M', null] : INSPECTOR_CARD_DOC_TYPES[cardKind as Exclude<InspectorCardKind, 'other'>]
    return types.map((docType) => [cardKind, docType] as const)
  })
  it.each(cardRows)('a %s card (%s) on an unassigned Flow has no switch', (cardKind, docType) => {
    expect(at({ cardKind, docType })).toEqual(HIDDEN)
  })

  it.each([
    ['no document bound (a Graph node before its run)', { hasDocument: false }],
    ['a requirement card without an R / B type', { docType: null }],
    ['a requirement card of another type', { docType: 'X' }],
  ] as const)('%s -> hidden', (_label, fields) => {
    expect(at(fields)).toEqual(HIDDEN)
  })

  it('the type is read case- and space-insensitively', () => {
    expect(at({ docType: ' b ' })).toEqual(ON)
  })
})

describe('D#1 §8.5 read-only required rows', () => {
  it.each([
    // graph × revision / draft: the switch left the "shown disabled" list (D#2 §3.5) — hidden in both
    ['graph × revision (readOnly revision)', { ownership: 'graph', readOnly: 'revision' }, HIDDEN],
    ['graph × draft (readOnly null)', { ownership: 'graph', readOnly: null }, HIDDEN],
    ['conflict (readOnly conflict)', { ownership: 'conflict', readOnly: 'conflict' }, HIDDEN],
    ['conflict × revision', { ownership: 'conflict', readOnly: 'conflict' }, HIDDEN],
    // where the switch is shown, a read-only view disables it with the shared reason keys
    ['unassigned × revision view', { readOnly: 'revision' }, OFF('reason.read_only_revision')],
    ['unassigned × conflict reason (as given)', { readOnly: 'conflict' }, OFF('reason.read_only_conflict')],
    ['unassigned × draft view', { readOnly: null }, ON],
  ] as const)('%s', (_label, fields, expected) => {
    expect(at(fields)).toEqual(expected)
  })
})

describe('one reason, in the order of D#1 §8.3 rule 6-1', () => {
  it.each([
    ['disposed', { groupDisposed: true }, 'reason.disposed'],
    ['AI running', { aiRunning: true }, 'reason.ai_running'],
    ['a switch in flight', { busy: true }, 'reason.busy'],
    ['conflict before everything', { readOnly: 'conflict', groupDisposed: true, aiRunning: true, busy: true }, 'reason.read_only_conflict'],
    ['revision before disposed', { readOnly: 'revision', groupDisposed: true, aiRunning: true }, 'reason.read_only_revision'],
    ['revision before AI running', { readOnly: 'revision', aiRunning: true }, 'reason.read_only_revision'],
    ['disposed before AI running', { groupDisposed: true, aiRunning: true, busy: true }, 'reason.disposed'],
    ['AI running before busy', { aiRunning: true, busy: true }, 'reason.ai_running'],
  ] as const)('%s', (_label, fields, reason) => {
    expect(at(fields)).toEqual(OFF(reason))
  })

  it('a hidden switch never carries a reason', () => {
    expect(at({ ownership: 'v01', groupDisposed: true, aiRunning: true, busy: true })).toEqual(HIDDEN)
  })
})

describe('the support fixtures as targets', () => {
  it.each([
    ['unassigned R root', 'unassigned', ON],
    ['v01 TR', 'v01', HIDDEN],
    ['v01 slot without a document', 'v01NoDocument', HIDDEN],
    ['graph TS', 'graph', HIDDEN],
    ['graph gate', 'graphGate', HIDDEN],
    ['conflict D', 'conflict', HIDDEN],
  ] as const)('%s', (_label, key, expected) => {
    const target = targetOf(SELECTIONS[key])
    expect(
      kindSwitchAvailability({
        cardKind: target.cardKind,
        docType: target.docType,
        hasDocument: !!target.docId,
        ownership: target.ownership,
        readOnly: target.readOnly,
        aiRunning: false,
        groupDisposed: false,
        busy: false,
      }).switch,
    ).toEqual(expected)
  })
})

describe('the kind a root switches to', () => {
  it.each([
    ['R', 'B'],
    ['B', 'R'],
    ['r', 'B'],
    [' B ', 'R'],
    ['N', null],
    ['', null],
    [null, null],
    [undefined, null],
  ] as const)('%s -> %s', (from, to) => {
    expect(kindSwitchTarget(from)).toBe(to)
  })
})

const FAILURES: readonly KindSwitchFailureKind[] = [
  'graph', 'ownership_conflict', 'decided', 'children', 'collision', 'disposed', 'running', 'conflict', 'invalid', 'forbidden', 'not_found', 'network', 'other',
]

describe('refusals', () => {
  it.each(FAILURES)('%s has the key failure.%s', (kind) => {
    expect(kindSwitchFailureKey(kind)).toBe(`failure.${kind}`)
  })

  it.each([
    ['graph', true],
    ['ownership_conflict', true],
    ['decided', true],
    ['children', true],
    ['collision', true],
    ['conflict', true],
    ['not_found', true],
    ['disposed', false],
    ['running', false],
    ['invalid', false],
    ['forbidden', false],
    ['network', false],
    ['other', false],
  ] as const)('%s reads the Canvas again: %s', (kind, refresh) => {
    expect(kindSwitchRefreshesFlow(kind)).toBe(refresh)
  })
})

describe('strings', () => {
  const lookup = (tree: unknown, key: string) => key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], tree)
  const KEYS = [
    'label', 'kinds.R', 'kinds.B', 'switch', 'switch_hint', 'done',
    'reason.read_only_conflict', 'reason.read_only_revision', 'reason.disposed', 'reason.ai_running', 'reason.busy',
    ...FAILURES.map(kindSwitchFailureKey),
  ]

  it.each(['ko', 'en', 'ja'] as const)('%s has every key the rules and the screen use', (locale) => {
    for (const key of KEYS) expect(typeof lookup(messages[locale], key), `${locale} ${key}`).toBe('string')
  })

  it.each(['ko', 'en', 'ja'] as const)('%s keeps no confirmation strings (0015-T T2: a pick switches at once)', (locale) => {
    expect(lookup(messages[locale], 'confirm')).toBeUndefined()
  })

  it('no undecided-feature words (B14)', () => {
    expect(JSON.stringify(messages)).not.toMatch(/메모|memo|AI 전달|ai_delivery|tsc|위임|delegat|전개|expand/i)
  })
})
