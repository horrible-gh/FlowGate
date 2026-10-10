/**
 * flowgate.v02.0048 T#9 — settingsModel as tables (D#1 §8.5; D#2 §2.4 rule 3, §4.1 test ①): ownership 4
 * kinds, the read-only rows, row states (not started · protected pending report · started), AI running,
 * disposed, the guard refusals, a save in flight, the review count 0 (reviewer off), nothing changed; the
 * row a slot names, the choices of each field, the draft, and what a running hop took from the row.
 */
import { describe, expect, it } from 'vitest'
import {
  SETTINGS_OPERATION_CLASS,
  SETTINGS_STORED_SOURCE,
  settingsAvailability,
  settingsBlockedBy,
  settingsChanged,
  settingsDraftOf,
  settingsDraftWith,
  settingsErrorKey,
  settingsFailureKey,
  settingsHopView,
  settingsProviderOptions,
  settingsReviewCountKey,
  settingsReviewCountOptions,
  settingsRowFor,
  settingsRowPhase,
  settingsRowState,
  type SettingsHopFacts,
  type SettingsInput,
  type SettingsRowFacts,
} from '@/features/designer/inspector/features/settings/settingsModel'
import messages from '@/features/designer/inspector/features/settings/messages'
import { SELECTIONS, targetOf } from '../support/inspectorSupport'

const ROW: SettingsRowFacts = {
  itemId: 126,
  itemSeq: 6,
  type: 'T',
  label: '작업지시',
  status: 'pending',
  protected: false,
  providerId: 'aip_haiku',
  providerDisplayName: 'Claude Haiku',
  providerRegistered: true,
  reviewCount: 2,
  reviewerProviderId: 'aip_sonnet',
  reviewerProviderDisplayName: 'Claude Sonnet',
}

const BASE: SettingsInput = {
  ownership: 'v01',
  readOnly: null,
  groupDisposed: false,
  aiRunning: false,
  blocked: null,
  row: 'editable',
  reviewCount: 2,
  changed: true,
  busy: false,
}

type Cell = [visible: boolean, enabled: boolean, reason: string | null]
const cell = (item: { visible: boolean; enabled: boolean; reason: string | null }): Cell => [item.visible, item.enabled, item.reason]
const H: Cell = [false, false, null]
const ON: Cell = [true, true, null]
const off = (reason: string): Cell => [true, false, reason]

describe('settingsModel — availability table (flowgate.v02.0048 T#9, D#1 §8.5)', () => {
  // [name, input change, provider, reviewCount, reviewer, save]
  it.each<[string, Partial<SettingsInput>, Cell, Cell, Cell, Cell]>([
    // ownership 4 kinds (rules 2-4): the v0.1 Node settings exist on a v0.1 Flow only.
    ['v01 · not started · changed', {}, ON, ON, ON, ON],
    ['graph (draft) — hidden', { ownership: 'graph' }, H, H, H, H],
    ['unassigned — hidden', { ownership: 'unassigned' }, H, H, H, H],
    ['conflict — hidden', { ownership: 'conflict', readOnly: 'conflict' }, H, H, H, H],
    ['no ownership — hidden', { ownership: null }, H, H, H, H],
    // §8.5 read-only rows: graph / unassigned × revision · draft, conflict × revision (hidden, so no reason).
    ['graph × revision — hidden, no reason', { ownership: 'graph', readOnly: 'revision' }, H, H, H, H],
    ['graph × draft — hidden', { ownership: 'graph', readOnly: null }, H, H, H, H],
    ['unassigned × revision — hidden', { ownership: 'unassigned', readOnly: 'revision' }, H, H, H, H],
    ['unassigned × draft — hidden', { ownership: 'unassigned', readOnly: null }, H, H, H, H],
    ['conflict × revision — hidden', { ownership: 'conflict', readOnly: 'conflict' }, H, H, H, H],
    // The read-only reasons and their order (rule 6-1), on the shown v0.1 settings.
    ['v01 · readOnly conflict', { readOnly: 'conflict' }, off('reason.read_only_conflict'), off('reason.read_only_conflict'), off('reason.read_only_conflict'), off('reason.read_only_conflict')],
    ['v01 · readOnly revision', { readOnly: 'revision' }, off('reason.read_only_revision'), off('reason.read_only_revision'), off('reason.read_only_revision'), off('reason.read_only_revision')],
    ['revision × AI running', { readOnly: 'revision', aiRunning: true }, off('reason.read_only_revision'), off('reason.read_only_revision'), off('reason.read_only_revision'), off('reason.read_only_revision')],
    ['revision × disposed', { readOnly: 'revision', groupDisposed: true }, off('reason.read_only_revision'), off('reason.read_only_revision'), off('reason.read_only_revision'), off('reason.read_only_revision')],
    ['conflict × disposed × running', { readOnly: 'conflict', groupDisposed: true, aiRunning: true }, off('reason.read_only_conflict'), off('reason.read_only_conflict'), off('reason.read_only_conflict'), off('reason.read_only_conflict')],
    // Common facts (rule 6), then this feature's own.
    ['disposed', { groupDisposed: true }, off('reason.disposed'), off('reason.disposed'), off('reason.disposed'), off('reason.disposed')],
    ['AI running', { aiRunning: true }, off('reason.ai_running'), off('reason.ai_running'), off('reason.ai_running'), off('reason.ai_running')],
    ['disposed wins over AI running', { groupDisposed: true, aiRunning: true }, off('reason.disposed'), off('reason.disposed'), off('reason.disposed'), off('reason.disposed')],
    ['refused: disposed (409 group_disposed)', { blocked: 'disposed' }, off('reason.disposed'), off('reason.disposed'), off('reason.disposed'), off('reason.disposed')],
    ['refused: AI run (423)', { blocked: 'running' }, off('reason.ai_running'), off('reason.ai_running'), off('reason.ai_running'), off('reason.ai_running')],
    ['AI running wins over a started row', { aiRunning: true, row: 'started' }, off('reason.ai_running'), off('reason.ai_running'), off('reason.ai_running'), off('reason.ai_running')],
    ['row started (D#2 E3)', { row: 'started' }, off('reason.started'), off('reason.started'), off('reason.started'), off('reason.started')],
    ['row not in the read', { row: null }, off('reason.no_row'), off('reason.no_row'), off('reason.no_row'), off('reason.no_row')],
    ['save in flight', { busy: true }, off('reason.busy'), off('reason.busy'), off('reason.busy'), off('reason.busy')],
    ['started wins over busy', { row: 'started', busy: true }, off('reason.started'), off('reason.started'), off('reason.started'), off('reason.started')],
    // The reviewer is off with no review; saving needs a change (D#2 §2.4 rule 5).
    ['review count 0 — reviewer off', { reviewCount: 0 }, ON, ON, off('reason.review_off'), ON],
    ['review count -1 (until it passes) — reviewer on', { reviewCount: -1 }, ON, ON, ON, ON],
    ['review count 1 — reviewer on', { reviewCount: 1 }, ON, ON, ON, ON],
    ['nothing changed — save off', { changed: false }, ON, ON, ON, off('reason.unchanged')],
    ['nothing changed, review 0', { changed: false, reviewCount: 0 }, ON, ON, off('reason.review_off'), off('reason.unchanged')],
    ['common reason wins over review off / unchanged', { aiRunning: true, changed: false, reviewCount: 0 }, off('reason.ai_running'), off('reason.ai_running'), off('reason.ai_running'), off('reason.ai_running')],
  ])('%s', (_name, change, provider, reviewCount, reviewer, save) => {
    const result = settingsAvailability({ ...BASE, ...change })
    expect([cell(result.provider), cell(result.reviewCount), cell(result.reviewer), cell(result.save)]).toEqual([provider, reviewCount, reviewer, save])
    expect(cell(result.read)).toEqual(ON)
  })

  it('reading is a query; setting and saving are changes (D#1 §8.3 rule 0)', () => {
    expect(SETTINGS_OPERATION_CLASS).toEqual({ read: 'query', provider: 'change', reviewCount: 'change', reviewer: 'change', save: 'change' })
  })

  it.each(['v01', 'v01NoDocument', 'graph', 'graphGate', 'unassigned', 'conflict'] as const)(
    'support selection %s: readOnly / ownership straight from the target',
    (name) => {
      const target = targetOf(SELECTIONS[name])
      const result = settingsAvailability({ ...BASE, ownership: target.ownership, readOnly: target.readOnly })
      expect(result.save.visible).toBe(target.ownership === 'v01')
      if (target.ownership === 'v01') expect(result.save.enabled).toBe(target.readOnly === null)
    },
  )
})

describe('settingsModel — the row (flowgate.v02.0048 T#9, D#2 §2.4)', () => {
  const rows: SettingsRowFacts[] = [
    { ...ROW, itemId: 124, itemSeq: 4, status: 'done', protected: true },
    { ...ROW, itemId: 125, itemSeq: 5, type: 'TR', status: 'pending', protected: true },
    ROW,
    { ...ROW, itemId: 127, itemSeq: 7, status: 'in_progress', protected: true },
  ]

  it.each<[string, { itemId: number } | null, number | null]>([
    ['a started row', { itemId: 124 }, 124],
    ['a protected pending report', { itemId: 125 }, 125],
    ['a row not started', { itemId: 126 }, 126],
    ['a slot the read does not hold (rows written again)', { itemId: 9999 }, null],
    ['no slot (free document)', null, null],
  ])('%s', (_name, slot, itemId) => {
    expect(settingsRowFor(rows, slot)?.itemId ?? null).toBe(itemId)
  })

  it('no rows read yet: no row', () => {
    expect(settingsRowFor(null, { itemId: 126 })).toBeNull()
  })

  it.each<[string, string, string]>([
    ['pending', 'pending', 'editable'],
    ['in_progress', 'in_progress', 'started'],
    ['done', 'done', 'started'],
    ['unknown status', 'archived', 'started'],
  ])('row state: %s -> %s', (_name, status, state) => {
    expect(settingsRowState({ status })).toBe(state)
  })

  it('a protected pending report row is editable (the server saves it in place, D#2 §2.4 rule 3)', () => {
    expect(settingsRowState(rows[1])).toBe('editable')
  })

  // flowgate.v02.0052 T#2: the server derives status from the result document (get_sequence_items):
  // none -> pending, approved -> done, anything else (running, in review, rejected) -> in_progress.
  it.each<[string, string]>([
    ['pending', 'pending'],
    ['in_progress', 'in_progress'],
    ['done', 'done'],
    ['archived', 'in_progress'],
  ])('row phase: %s -> %s (only done reads as done)', (status, phase) => {
    expect(settingsRowPhase({ status })).toBe(phase)
  })
})

describe('settingsModel — draft and choices (flowgate.v02.0048 T#9)', () => {
  const providers = [
    { id: 'aip_opus', name: 'Claude Opus' },
    { id: 'aip_sonnet', name: 'Claude Sonnet' },
  ]

  it('the draft starts as the row; nothing changed', () => {
    const draft = settingsDraftOf(ROW)
    expect(draft).toEqual({ providerId: 'aip_haiku', providerDisplayName: 'Claude Haiku', reviewCount: 2, reviewerProviderId: 'aip_sonnet', reviewerProviderDisplayName: 'Claude Sonnet' })
    expect(settingsChanged(ROW, draft)).toBe(false)
    expect(settingsChanged(ROW, null)).toBe(false)
  })

  it.each<[string, 'provider' | 'reviewCount' | 'reviewer', string, Partial<ReturnType<typeof settingsDraftOf>>, boolean]>([
    ['run AI -> an offered AI takes its name', 'provider', 'aip_opus', { providerId: 'aip_opus', providerDisplayName: 'Claude Opus' }, true],
    ['run AI -> default', 'provider', '', { providerId: null, providerDisplayName: null }, true],
    ['run AI -> the stored one again', 'provider', 'aip_haiku', { providerId: 'aip_haiku', providerDisplayName: 'Claude Haiku' }, false],
    ['review count -> until it passes', 'reviewCount', '-1', { reviewCount: -1 }, true],
    ['review count -> none', 'reviewCount', '0', { reviewCount: 0 }, true],
    ['review count -> not a number keeps the draft', 'reviewCount', 'x', {}, false],
    ['reviewer -> default', 'reviewer', '', { reviewerProviderId: null, reviewerProviderDisplayName: null }, true],
    ['reviewer -> an offered AI', 'reviewer', 'aip_opus', { reviewerProviderId: 'aip_opus', reviewerProviderDisplayName: 'Claude Opus' }, true],
  ])('%s', (_name, field, value, expected, changed) => {
    const draft = settingsDraftWith(settingsDraftOf(ROW), field, value, providers)
    expect(draft).toEqual({ ...settingsDraftOf(ROW), ...expected })
    expect(settingsChanged(ROW, draft)).toBe(changed)
  })

  it('AI options: default first, then the offered AI; a stored AI no longer offered stays, marked', () => {
    expect(settingsProviderOptions(providers, { id: 'aip_haiku', name: 'Claude Haiku' })).toEqual([
      { value: '', key: 'option.default', params: {}, label: null },
      { value: 'aip_opus', key: null, params: {}, label: 'Claude Opus' },
      { value: 'aip_sonnet', key: null, params: {}, label: 'Claude Sonnet' },
      { value: 'aip_haiku', key: 'option.missing', params: { name: 'Claude Haiku' }, label: null },
    ])
    expect(settingsProviderOptions(providers, { id: null, name: null }).map((option) => option.value)).toEqual(['', 'aip_opus', 'aip_sonnet'])
    expect(settingsProviderOptions(providers, { id: 'aip_sonnet', name: 'x' }).map((option) => option.value)).toEqual(['', 'aip_opus', 'aip_sonnet'])
    expect(settingsProviderOptions([], { id: 'aip_x', name: null })[1]).toEqual({ value: 'aip_x', key: 'option.missing', params: { name: 'aip_x' }, label: null })
  })

  it('review count options: the server set; a stored count outside it is kept, marked', () => {
    expect(settingsReviewCountOptions([-1, 0, 1, 2, 3, 4], 2).map((option) => [option.value, option.key])).toEqual([
      ['-1', 'count.until_pass'],
      ['0', 'count.none'],
      ['1', 'count.times'],
      ['2', 'count.times'],
      ['3', 'count.times'],
      ['4', 'count.times'],
    ])
    expect(settingsReviewCountOptions([-1, 0, 1, 2, 3], 7).at(-1)).toEqual({ value: '7', key: 'count.out_of_range', params: { n: 7 }, label: null })
    expect([-1, 0, 3].map(settingsReviewCountKey)).toEqual(['count.until_pass', 'count.none', 'count.times'])
  })
})

describe('settingsModel — what a running hop took from the row (flowgate.v02.0048 T#9, D#2 §2.1 / §4.1 ⑤)', () => {
  const HOP: SettingsHopFacts = {
    itemSeq: 6,
    providerId: 'aip_haiku',
    providerName: 'Claude Haiku',
    providerSource: SETTINGS_STORED_SOURCE,
    reviewCount: 2,
    reviewerProviderId: 'aip_sonnet',
    reviewerProviderName: 'Claude Sonnet',
  }

  it.each<[string, Partial<SettingsRowFacts>, Partial<SettingsHopFacts> | null, [string, string, string] | null]>([
    ['start answer: stored_sequence, same count and reviewer', {}, {}, ['row', 'row', 'row']],
    ['start answer: a request pick (selected_provider_source other)', {}, { providerSource: 'request_step', providerId: 'aip_opus' }, ['other', 'row', 'row']],
    ['start answer: a request pick of the same AI is still the request’s', {}, { providerSource: 'request_step' }, ['other', 'row', 'row']],
    ['start answer: the source decides over the AI id', {}, { providerId: 'aip_opus' }, ['row', 'row', 'row']],
    ['start answer: review count differs (a request override)', {}, { reviewCount: 1 }, ['row', 'other', 'row']],
    ['start answer: reviewer differs', {}, { reviewerProviderId: 'aip_opus' }, ['row', 'row', 'other']],
    ['status (no source): same AI id', {}, { providerSource: null }, ['row', 'row', 'row']],
    ['status (no source): another AI', {}, { providerSource: null, providerId: 'aip_opus' }, ['other', 'row', 'row']],
    ['status (no source): row leaves the run AI to the default', { providerId: null }, { providerSource: null }, ['default', 'row', 'row']],
    ['status (no source): no AI in the answer', {}, { providerSource: null, providerId: null }, ['unknown', 'row', 'row']],
    ['row leaves the reviewer to the default', { reviewerProviderId: null }, {}, ['row', 'row', 'default']],
    ['no review on both sides: reviewer as saved', { reviewCount: 0 }, { reviewCount: 0, reviewerProviderId: null }, ['row', 'row', 'row']],
    ['hop without a count', {}, { reviewCount: null }, ['row', 'unknown', 'row']],
    ['the hop of another row', {}, { itemSeq: 7 }, null],
    ['a hop without item_seq', {}, { itemSeq: null }, null],
    ['no hop', {}, null, null],
  ])('%s', (_name, row, hop, expected) => {
    const view = settingsHopView({ ...ROW, ...row }, hop === null ? null : { ...HOP, ...hop })
    expect(view ? [view.provider, view.reviewCount, view.reviewer] : null).toEqual(expected)
  })

  it('names and count come from the hop', () => {
    expect(settingsHopView(ROW, HOP)).toMatchObject({ providerName: 'Claude Haiku', count: 2, reviewerName: 'Claude Sonnet' })
    expect(settingsHopView(ROW, { ...HOP, providerName: null, reviewerProviderName: null })).toMatchObject({ providerName: 'aip_haiku', reviewerName: 'aip_sonnet' })
  })
})

describe('settingsModel — refusals and keys (flowgate.v02.0048 T#9)', () => {
  it.each<[string, 'disposed' | 'running' | null]>([
    ['disposed', 'disposed'],
    ['running', 'running'],
    ['owner', null],
    ['stale', null],
    ['not_decided', null],
    ['invalid', null],
    ['forbidden', null],
    ['not_found', null],
    ['network', null],
    ['other', null],
  ])('refusal %s blocks %s', (kind, blocked) => {
    expect(settingsBlockedBy(kind as Parameters<typeof settingsBlockedBy>[0])).toBe(blocked)
    expect(settingsFailureKey(kind as Parameters<typeof settingsFailureKey>[0])).toBe(`failure.${kind}`)
  })

  it.each([
    ['forbidden', 'error.forbidden'],
    ['not_found', 'error.not_found'],
    ['network', 'error.network'],
    ['not_decided', 'error.not_decided'],
    ['stale', 'error.other'],
    ['other', 'error.other'],
  ])('read error %s -> %s', (kind, key) => {
    expect(settingsErrorKey(kind as Parameters<typeof settingsErrorKey>[0])).toBe(key)
  })

  it('every key the rules produce has a ko / en / ja string; no undecided item is worded', () => {
    const keys = [
      ...['read_only_conflict', 'read_only_revision', 'disposed', 'ai_running', 'no_row', 'started', 'busy', 'review_off', 'unchanged'].map((key) => `reason.${key}`),
      ...['disposed', 'running', 'owner', 'stale', 'not_decided', 'invalid', 'forbidden', 'not_found', 'network', 'other'].map((key) => `failure.${key}`),
      ...['not_decided', 'forbidden', 'not_found', 'network', 'other'].map((key) => `error.${key}`),
      'option.default', 'option.missing', 'count.until_pass', 'count.none', 'count.times', 'count.out_of_range',
      ...['row', 'other', 'default', 'unknown'].map((key) => `hop.match.${key}`),
    ]
    for (const locale of ['ko', 'en', 'ja'] as const) {
      for (const key of keys) {
        const value = key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], messages[locale])
        expect(typeof value, `${locale} ${key}`).toBe('string')
      }
    }
    // flowgate.v02.0051 stage 2 (B14 as turn 7 reset it): the v7 rows that are not stored on a Node (지시서 사용 /
    // 생략 · 검수 뒤 진행) are worded and drawn inactive with their reason; restart / time limit / review criteria
    // are the Frame's run settings strings, not this feature's.
    for (const key of ['instruction.label', 'instruction.use', 'instruction.omit', 'policy.label', 'policy.follow', 'policy.human', 'unconnected']) {
      for (const locale of ['ko', 'en', 'ja'] as const) {
        const value = key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], messages[locale])
        expect(typeof value, `${locale} ${key}`).toBe('string')
      }
    }
    const text = JSON.stringify(messages.ko)
    for (const word of ['재시작', '제한 시간', '검수 기준']) expect(text).not.toContain(word)
  })
})
