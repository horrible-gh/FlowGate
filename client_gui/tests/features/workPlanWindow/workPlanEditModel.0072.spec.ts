/**
 * flowgate.v02.0072 T#1 — the rules of the v0.2 work plan edit window (workPlanEditModel), checked against what the
 * v0.1 WorkPlanEditor does and what the server's PUT …/work-plan takes (work_plan_service.validate):
 * - the body goes back whole: fields with no control (candidates, x_* extensions, review, pre-instruction) are kept;
 * - a quantity change edits that type's cards in place and renumbers keys / ordinals / pair keys by card order;
 *   a new card has no card_id and no AI (never defaults.provider_id); a removed card comes back with its values;
 *   a started card is never removed or moved, nothing goes in front of it;
 * - [apply to all] copies the default AI / note onto every unlocked Step, nothing else;
 * - the Step window boundary edits one row by identity, never another; a save trades temporary ids for the server's.
 */
import { describe, expect, it } from 'vitest'
import {
  applyDefaults,
  applyQuantity,
  applyStepDraft,
  bodyOf,
  canMoveCardBy,
  draftOf,
  editBodyOf,
  errorsByRow,
  moveCard,
  planQuantity,
  providerOptions,
  quantityBoxes,
  rowsOf,
  savedDraft,
  setStepNote,
  setStepProvider,
  stepDraftOf,
  stepPlace,
  toWorkPlanEditView,
  totalsOf,
  unassignedSteps,
  workPlanLockReason,
  workPlanRowId,
  type WorkPlanDraft,
} from '@/features/workPlanWindow/workPlanEditModel'
import { DOC, planBody, readAnswer } from './workPlanFixtures'

const STARTED = new Set(['c_d1'])
const name = (id: string) => ({ aip_opus: 'Opus', aip_sonnet: 'Sonnet' })[id] ?? null

function draft(): WorkPlanDraft {
  return draftOf(toWorkPlanEditView(readAnswer()).body)
}

const baseSteps = () => planBody().steps as Record<string, unknown>[]
const keys = (d: WorkPlanDraft) => bodyOf(d).steps.map((s) => s.key)
const pairs = (d: WorkPlanDraft) => bodyOf(d).steps.map((s) => `${s.key}>${s.pair_key ?? '-'}`)

describe('read view (GET …/work-plan)', () => {
  it('reads lock, limits, choices, registered AIs and started cards; the body stays whole', () => {
    const view = toWorkPlanEditView(readAnswer())
    expect(view.docId).toBe(DOC)
    expect(view.revisionNo).toBe(2)
    expect(view.editable).toBe(true)
    expect(view.editLockedReason).toBeNull()
    expect(view.noteMaxChars).toBe(200)
    expect(view.reviewCountChoices).toEqual([0, 1, 2, 3, -1])
    expect(view.registered?.map((p) => p.id)).toEqual(['aip_opus', 'aip_sonnet'])
    expect([...view.startedCardIds]).toEqual(['c_d1'])
    expect(view.body.x_origin_note).toEqual({ kept: true })
    expect(view.body.wp_version).toBe(2)
  })

  it('a locked answer keeps its reason; an answer without editable is editable (the server decides)', () => {
    const locked = toWorkPlanEditView(readAnswer({ editable: false, edit_locked_reason: 'final_approved' }))
    expect(locked.editable).toBe(false)
    expect(locked.editLockedReason).toBe('final_approved')
    const old = toWorkPlanEditView({ ...readAnswer(), editable: undefined })
    expect(old.editable).toBe(true)
    expect(toWorkPlanEditView({ body: {} }).reviewCountChoices).toEqual([0])
  })
})

describe('the body goes back whole', () => {
  it('an untouched draft sends the same body (every field and extension kept)', () => {
    expect(bodyOf(draft())).toEqual(editBodyOf(planBody()))
    expect(bodyOf(draft())).toEqual(planBody())
  })

  it('cards: a set is one card, a sheet one card; ids are the card ids', () => {
    const d = draft()
    expect(d.cards.map((c) => [c.id, c.steps.map((s) => s.key)])).toEqual([
      ['c_d1', ['D#1']],
      ['c_t1', ['T#1', 'TR#1']],
      ['c_t2', ['T#2', 'TR#2']],
      ['c_ts1', ['TS#1', 'TSR#1']],
    ])
    expect(rowsOf(d).map((r) => r.rowId)).toEqual([
      'c_d1|main', 'c_t1|main', 'c_t1|result', 'c_t2|main', 'c_t2|result', 'c_ts1|main', 'c_ts1|result',
    ])
  })

  it('totals and unassigned follow the draft', () => {
    const d = draft()
    expect(totalsOf(d)).toEqual({ designSheets: 1, workSets: 3, steps: 7 })
    // T#1, TR#2 have no AI; TSR#1 is the server's
    expect(unassignedSteps(d)).toBe(2)
  })

  it('quantity boxes: the counted types in body order, then known types it does not count', () => {
    const body = editBodyOf({ ...planBody(), counted_types: ['T', 'D'], quantities: { T: { unit: 'set', count: 2 }, D: { unit: 'sheet', count: 1 } } })
    expect(quantityBoxes(body).map((b) => `${b.type}:${b.count}`)).toEqual(['T:2', 'D:1', 'DS:0', 'P:0', 'L:0', 'DB:0', 'N:0', 'T2:0', 'TS:0'])
  })

  it('the place of a Step: n/total sheets, n sheet, n-th set', () => {
    const d = draft()
    const body = d.body
    expect(stepPlace(body, d.cards[0].steps[0])).toMatchObject({ key: 'sheet', n: 1 })
    expect(stepPlace(body, d.cards[2].steps[1])).toMatchObject({ key: 'set', n: 2 })
  })
})

describe('quantities (v0.1 setQuantity, 0649 T#3)', () => {
  it('growing appends a new card at the end: no card_id, no AI — never the default AI', () => {
    const d = applyQuantity(draft(), 'T', 3, STARTED)
    const body = bodyOf(d)
    expect(body.quantities.T).toEqual({ unit: 'set', count: 3 })
    expect(keys(d)).toEqual(['D#1', 'T#1', 'TR#1', 'T#2', 'TR#2', 'TS#1', 'TSR#1', 'T#3', 'TR#3'])
    const added = body.steps.slice(-2)
    expect(added.map((s) => [s.card_id ?? null, s.provider_id, s.pair_role, s.pair_key])).toEqual([
      [null, null, 'instruction', 'TR#3'],
      [null, null, 'result', 'T#3'],
    ])
    expect(body.defaults.provider_id).toBe('aip_opus')
    expect(unassignedSteps(d)).toBe(4)
  })

  it('a sheet type that was not counted joins counted_types and quantities', () => {
    const base = draftOf(editBodyOf({ ...planBody(), counted_types: ['D', 'T', 'TS'], quantities: { D: { unit: 'sheet', count: 1 }, T: { unit: 'set', count: 2 }, TS: { unit: 'set', count: 1 } } }))
    const d = applyQuantity(base, 'P', 2, STARTED)
    const body = bodyOf(d)
    expect(body.counted_types).toEqual(['D', 'T', 'TS', 'P'])
    expect(body.quantities.P).toEqual({ unit: 'sheet', count: 2 })
    expect(keys(d).slice(-2)).toEqual(['P#1', 'P#2'])
    expect(body.steps.slice(-2).every((s) => s.pair_role === 'single' && s.pair_key === null)).toBe(true)
  })

  it('a TS set builds its TSR result locked, as the server does', () => {
    const d = applyQuantity(draft(), 'TS', 2, STARTED)
    const last = bodyOf(d).steps.slice(-1)[0]
    expect(last).toMatchObject({ key: 'TSR#2', locked: true, locked_reason: 'server_assembled', origin: 'system' })
  })

  it('shrinking removes the last cards of the type; Steps with values ask first (count)', () => {
    expect(planQuantity(draft(), 'T', 1, STARTED)).toEqual({ status: 'ok', count: 1, valueSteps: 1 })
    expect(planQuantity(draft(), 'T', 0, STARTED)).toEqual({ status: 'ok', count: 0, valueSteps: 3 })
    const d = applyQuantity(draft(), 'T', 1, STARTED)
    expect(keys(d)).toEqual(['D#1', 'T#1', 'TR#1', 'TS#1', 'TSR#1'])
  })

  it('the removed card comes back with its id and values when raised again in the session', () => {
    const shrunk = applyQuantity(draft(), 'T', 0, STARTED)
    expect(keys(shrunk)).toEqual(['D#1', 'TS#1', 'TSR#1'])
    const back = applyQuantity(shrunk, 'T', 2, STARTED)
    expect(bodyOf(back)).toEqual(planBody())
  })

  it('a started card is never removed: a decrease that needs it is blocked', () => {
    expect(planQuantity(draft(), 'D', 0, STARTED).status).toBe('blocked_started')
    expect(applyQuantity(draft(), 'D', 0, STARTED)).toEqual(draft())
    expect(planQuantity(draft(), 'D', 0, new Set()).status).toBe('ok')
  })

  it('clamped to 0..20; the same count is unchanged', () => {
    expect(planQuantity(draft(), 'T', 2, STARTED).status).toBe('unchanged')
    expect(planQuantity(draft(), 'T', 99, STARTED).count).toBe(20)
    expect(planQuantity(draft(), 'N', -3, STARTED).status).toBe('unchanged')
  })
})

describe('card moves (O7: started cards pinned)', () => {
  it('moves a card and renumbers keys / ordinals / pair keys by order; card ids and values travel', () => {
    const result = moveCard(draft(), 2, 1, STARTED)
    expect(result.status).toBe('moved')
    if (result.status !== 'moved') return
    expect(pairs(result.draft)).toEqual(['D#1>-', 'T#1>TR#1', 'TR#1>T#1', 'T#2>TR#2', 'TR#2>T#2', 'TS#1>TSR#1', 'TSR#1>TS#1'])
    const steps = bodyOf(result.draft).steps
    expect(steps[1]).toMatchObject({ card_id: 'c_t2', provider_id: 'aip_old', ordinal: 1 })
    expect(steps[3]).toMatchObject({ card_id: 'c_t1', review_count: 1, pre_instruction_text: '사전 지시', ordinal: 2 })
  })

  it('a started card does not move and nothing goes in front of it', () => {
    expect(moveCard(draft(), 0, 2, STARTED).status).toBe('started')
    expect(moveCard(draft(), 1, 0, STARTED).status).toBe('before_started')
    expect(canMoveCardBy(draft(), 1, -1, STARTED)).toBe(false)
    expect(canMoveCardBy(draft(), 1, 1, STARTED)).toBe(true)
    expect(canMoveCardBy(draft(), 3, 1, STARTED)).toBe(false)
    expect(canMoveCardBy(draft(), 1, -1, new Set())).toBe(true)
  })
})

describe('per-Step values and defaults', () => {
  it('AI and note of one row only; a locked Step takes neither', () => {
    let d = setStepProvider(draft(), 'c_t1|main', 'aip_sonnet', 'Sonnet')
    d = setStepNote(d, 'c_t2|result', '보고')
    const steps = bodyOf(d).steps
    expect(steps[1]).toMatchObject({ provider_id: 'aip_sonnet', provider_display_name: 'Sonnet', origin: 'human' })
    expect(steps[4]).toMatchObject({ note: '보고' })
    expect(steps.filter((s, i) => i !== 1 && i !== 4)).toEqual(baseSteps().filter((_, i) => i !== 1 && i !== 4))
    const locked = setStepProvider(draft(), 'c_ts1|result', 'aip_opus', 'Opus')
    expect(locked).toEqual(draft())
  })

  it('[apply to all] copies the default AI and note onto every unlocked Step, review and pre-instruction stay', () => {
    const d = applyDefaults(draft(), name)
    const steps = bodyOf(d).steps
    for (const s of steps.filter((item) => !item.locked)) expect(s).toMatchObject({ provider_id: 'aip_opus', provider_display_name: 'Opus', note: '기본 메모', origin: 'human' })
    expect(steps[6]).toEqual(baseSteps()[6])
    expect(steps[1]).toMatchObject({ review_count: 1, reviewer_provider_id: 'aip_sonnet', pre_instruction_text: '사전 지시' })
  })

  it('AI options: unassigned first, the registered AIs, then a used AI no longer registered, marked', () => {
    const view = toWorkPlanEditView(readAnswer())
    const options = providerOptions(view, ['aip_old', 'aip_opus', null], '담당 미지정', (n) => `${n} (사용 불가)`)
    expect(options).toEqual([
      { value: '', label: '담당 미지정' },
      { value: 'aip_opus', label: 'Opus' },
      { value: 'aip_sonnet', label: 'Sonnet' },
      { value: 'aip_old', label: 'Old AI (사용 불가)' },
    ])
    const old = toWorkPlanEditView({ ...readAnswer(), registered_providers: undefined })
    expect(providerOptions(old, [], '-', (n) => n).map((o) => o.value)).toEqual(['', 'aip_old'])
  })
})

describe('the Step window boundary (T#2)', () => {
  it('a copy of the row values; locked or missing rows give none', () => {
    expect(stepDraftOf(draft(), 'c_t1|main')).toEqual({
      reviewCount: 1,
      reviewerProviderId: 'aip_sonnet',
      preInstructionText: '사전 지시',
      preInstructionAttachment: { doc_id: DOC, filename: 'r_1.txt', original_filename: 'brief.txt', content_sha256: 'a'.repeat(64) },
    })
    expect(stepDraftOf(draft(), 'c_ts1|result')).toBeNull()
    expect(stepDraftOf(draft(), 'nope|main')).toBeNull()
  })

  it('apply puts the values on that row only; review 0 clears the reviewer', () => {
    const result = applyStepDraft(draft(), 'c_t2|main', { reviewCount: 0, reviewerProviderId: 'aip_opus', preInstructionText: '새 지시', preInstructionAttachment: null }, name)
    expect(result.status).toBe('applied')
    if (result.status !== 'applied') return
    const steps = bodyOf(result.draft).steps
    expect(steps[3]).toMatchObject({ review_count: 0, reviewer_provider_id: null, pre_instruction_text: '새 지시' })
    expect(steps.filter((_, i) => i !== 3)).toEqual(baseSteps().filter((_, i) => i !== 3))
  })

  it('a result Step takes a review but no pre-instruction; a moved card keeps its row', () => {
    expect(applyStepDraft(draft(), 'c_t1|result', { reviewCount: 2, reviewerProviderId: null, preInstructionText: 'x', preInstructionAttachment: null }, name).status).toBe('not_eligible')
    const review = applyStepDraft(draft(), 'c_t1|result', { reviewCount: 2, reviewerProviderId: 'aip_sonnet', preInstructionText: null, preInstructionAttachment: null }, name)
    expect(review.status).toBe('applied')
    const moved = moveCard(draft(), 2, 1, STARTED)
    if (moved.status !== 'moved') throw new Error('not moved')
    const applied = applyStepDraft(moved.draft, 'c_t1|main', { reviewCount: 3, reviewerProviderId: null, preInstructionText: '그대로', preInstructionAttachment: null }, name)
    if (applied.status !== 'applied') throw new Error('not applied')
    const t1 = bodyOf(applied.draft).steps.find((s) => s.card_id === 'c_t1' && s.pair_role === 'instruction')
    expect(t1).toMatchObject({ key: 'T#2', review_count: 3, pre_instruction_text: '그대로' })
    expect(applyStepDraft(draft(), 'c_ts1|result', { reviewCount: 1, reviewerProviderId: null, preInstructionText: null, preInstructionAttachment: null }, name).status).toBe('locked')
    expect(applyStepDraft(draft(), 'gone|main', { reviewCount: 1, reviewerProviderId: null, preInstructionText: null, preInstructionAttachment: null }, name).status).toBe('gone')
  })

  it('a legacy body without card ids gets the same row ids on every read, and none is sent', () => {
    const legacy = planBody() as Record<string, any>
    for (const s of legacy.steps) delete s.card_id
    const first = draftOf(editBodyOf(legacy))
    const again = draftOf(editBodyOf(legacy))
    expect(first.cards.map((c) => c.id)).toEqual(['legacy:D#1', 'legacy:T#1', 'legacy:T#2', 'legacy:TS#1'])
    expect(again.cards.map((c) => c.id)).toEqual(first.cards.map((c) => c.id))
    expect(bodyOf(first).steps.every((s) => !('card_id' in s))).toBe(true)
    const moved = moveCard(first, 2, 1, new Set())
    if (moved.status !== 'moved') throw new Error('not moved')
    expect(rowsOf(moved.draft).find((r) => r.rowId === 'legacy:T#2|main')?.step.key).toBe('T#1')
  })

  it('after a save the temporary ids are traded for the server card ids by position', () => {
    const grown = applyQuantity(draft(), 'T', 3, STARTED)
    const tmp = grown.cards[4].id
    expect(tmp.startsWith('tmp:')).toBe(true)
    const saved = bodyOf(grown)
    saved.steps[7].card_id = 'c_new'
    saved.steps[8].card_id = 'c_new'
    const { draft: next, renamed } = savedDraft(grown, saved)
    expect(next.cards[4].id).toBe('c_new')
    expect(renamed.get(workPlanRowId(tmp, 'main'))).toBe('c_new|main')
    expect(renamed.get(workPlanRowId(tmp, 'result'))).toBe('c_new|result')
    expect(renamed.size).toBe(2)
  })
})

describe('lock reason and save errors', () => {
  it('first reason first', () => {
    const base = { external: null, aiRunning: false, uploadGuarded: false, editable: true, editLockedReason: null }
    expect(workPlanLockReason(base)).toBeNull()
    expect(workPlanLockReason({ ...base, editable: false, editLockedReason: 'final_approved' })).toBe('final_approved')
    expect(workPlanLockReason({ ...base, editable: false, editLockedReason: 'status' })).toBe('status')
    expect(workPlanLockReason({ ...base, editable: false, uploadGuarded: true })).toBe('upload')
    expect(workPlanLockReason({ ...base, uploadGuarded: true, aiRunning: true })).toBe('ai_running')
    expect(workPlanLockReason({ ...base, aiRunning: true, external: 'run_locked' })).toBe('run_locked')
    expect(workPlanLockReason({ ...base, external: 'read_only' })).toBe('read_only')
    // a save the server refused while its read still says editable
    expect(workPlanLockReason({ ...base, refused: 'disposed' })).toBe('disposed')
    expect(workPlanLockReason({ ...base, refused: 'not_editable' })).toBe('not_editable')
    expect(workPlanLockReason({ ...base, refused: 'not_editable', editable: false, editLockedReason: 'final_approved' })).toBe('final_approved')
    expect(workPlanLockReason({ ...base, refused: 'disposed', aiRunning: true })).toBe('ai_running')
  })

  it('field errors are pinned to the row of the Step key the server named; others go on top', () => {
    const errors = [
      { loc: 'steps[3].note', key: 'T#2', code: 'note_too_long', params: {}, msg: 'long' },
      { loc: 'quantities', key: null, code: 'steps_quantity_mismatch', params: {}, msg: 'mismatch' },
      { loc: 'steps[9]', key: 'X#9', code: 'duplicate_key', params: {}, msg: 'dup' },
    ]
    const pinned = errorsByRow(draft(), errors)
    expect(Object.keys(pinned.rows)).toEqual(['c_t2|main'])
    expect(pinned.top.map((e) => e.code)).toEqual(['steps_quantity_mismatch', 'duplicate_key'])
  })
})
