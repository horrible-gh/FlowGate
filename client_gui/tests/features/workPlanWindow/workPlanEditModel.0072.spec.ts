/**
 * flowgate.v02.0072 T#1 — the rules of the v0.2 work plan edit window (workPlanEditModel), checked against what the
 * v0.1 WorkPlanEditor does and what the server's PUT …/work-plan takes (work_plan_service.validate):
 * - the body goes back whole: fields with no control (candidates, x_* extensions, review, pre-instruction) are kept;
 * - a quantity change edits that type's cards in place and renumbers keys / ordinals / pair keys by card order;
 *   a new card has no card_id and no AI (never defaults.provider_id); a removed card comes back with its values;
 *   a started card is never removed or moved, nothing goes in front of it;
 * - [apply to all] copies the default AI / note onto every unlocked Step, nothing else;
 * - the Step window boundary edits one row by identity, never another; a save trades temporary ids for the server's.
 * flowgate.v02.0073 T#1 — DS / T2 are not drawn (quantity boxes, cards, the shown totals), and that is all: the body
 * keeps them whole, [apply to all] still reaches their unlocked Steps, a move among the shown cards (the screen's
 * positions) leaves every hidden card in its slot with its key; a type's icon is the Designer Palette's.
 * flowgate.v02.0073 T#2 — a row's review is set by the review popover only (setStepReview: that row, 0 clears the
 * reviewer, a locked Step takes none; the picker's counts are the server's); the Step window's draft is the
 * pre-instruction and attachment only, so its apply never touches a review; the popover's place stays on screen
 * (every path clamped — also for a button off the viewport's side).
 */
import { describe, expect, it } from 'vitest'
import { DEFAULT_PALETTE_ITEMS } from '@/features/designer/palette/paletteCatalog'
import {
  applyDefaults,
  applyQuantity,
  applyStepDraft,
  bodyOf,
  canMoveCardBy,
  canMoveShownCardBy,
  draftOf,
  editBodyOf,
  errorsByRow,
  hiddenRowIds,
  moveCard,
  moveShownCard,
  planQuantity,
  providerOptions,
  quantityBoxes,
  reviewCountOptions,
  rowsOf,
  savedDraft,
  setStepNote,
  setStepProvider,
  setStepReview,
  shownCardsOf,
  stepDraftOf,
  stepKeyShown,
  stepPlace,
  toWorkPlanEditView,
  totalsOf,
  unassignedByShown,
  unassignedSteps,
  workPlanLockReason,
  workPlanRowId,
  workPlanTypeIcon,
  type WorkPlanDraft,
} from '@/features/workPlanWindow/workPlanEditModel'
import { REVIEW_POPOVER_MARGIN, reviewPopoverPlace } from '@/features/workPlanWindow/workPlanReviewPopover'
import { DOC, hiddenPlanBody, hiddenSteps, planBody, readAnswer } from './workPlanFixtures'

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

  it('quantity boxes: the counted types in body order, then known types it does not count — DS / T2 not drawn (0073 T#1)', () => {
    const body = editBodyOf({ ...planBody(), counted_types: ['T', 'D'], quantities: { T: { unit: 'set', count: 2 }, D: { unit: 'sheet', count: 1 } } })
    expect(quantityBoxes(body).map((b) => `${b.type}:${b.count}`)).toEqual(['T:2', 'D:1', 'P:0', 'L:0', 'DB:0', 'N:0', 'TS:0'])
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

describe('a row’s review (0073 T#2, the review popover)', () => {
  it('sets that row’s count and reviewer only, as a person’s value; the reviewer name is the AI’s', () => {
    const d = setStepReview(draft(), 'c_t2|main', 2, 'aip_opus', name)
    const steps = bodyOf(d).steps
    expect(steps[3]).toMatchObject({ review_count: 2, reviewer_provider_id: 'aip_opus', reviewer_provider_display_name: 'Opus', origin: 'human' })
    expect(steps[3]).toMatchObject({ pre_instruction_text: null, note: null, provider_id: 'aip_old' })
    expect(steps.filter((_, i) => i !== 3)).toEqual(baseSteps().filter((_, i) => i !== 3))
    const project = setStepReview(draft(), 'c_t2|main', -1, null, name)
    expect(bodyOf(project).steps[3]).toMatchObject({ review_count: -1, reviewer_provider_id: null, reviewer_provider_display_name: null })
  })

  it('a count of 0 clears the reviewer (the server refuses one without review)', () => {
    const d = setStepReview(draft(), 'c_t1|main', 0, 'aip_opus', name)
    expect(bodyOf(d).steps[1]).toMatchObject({ review_count: 0, reviewer_provider_id: null, reviewer_provider_display_name: null, pre_instruction_text: '사전 지시' })
  })

  it('a result Step takes a review; a locked or missing row, a non-integer count or the same values change nothing', () => {
    const report = setStepReview(draft(), 'c_t1|result', 3, 'aip_sonnet', name)
    expect(bodyOf(report).steps[2]).toMatchObject({ key: 'TR#1', review_count: 3, reviewer_provider_id: 'aip_sonnet', reviewer_provider_display_name: 'Sonnet' })
    const base = draft()
    expect(setStepReview(base, 'c_ts1|result', 1, null, name)).toBe(base)
    expect(setStepReview(base, 'gone|main', 1, null, name)).toBe(base)
    expect(setStepReview(base, 'c_t1|main', 1.5, null, name)).toBe(base)
    expect(setStepReview(base, 'c_t1|main', 1, 'aip_sonnet', name)).toBe(base)
  })

  it('follows the row after a card move (the key changes, the row does not)', () => {
    const moved = moveCard(draft(), 2, 1, STARTED)
    if (moved.status !== 'moved') throw new Error('not moved')
    const d = setStepReview(moved.draft, 'c_t2|main', 1, 'aip_opus', name)
    const t2 = bodyOf(d).steps.find((s) => s.card_id === 'c_t2' && s.pair_role === 'instruction')
    expect(t2).toMatchObject({ key: 'T#1', review_count: 1, reviewer_provider_id: 'aip_opus' })
    expect(bodyOf(d).steps.find((s) => s.card_id === 'c_t1' && s.pair_role === 'instruction')).toMatchObject({ review_count: 1, reviewer_provider_id: 'aip_sonnet' })
  })

  it('count options: the server’s choices in order, and the row’s own count when no longer offered', () => {
    const label = (n: number) => (n === 0 ? '검수 없음' : n < 0 ? '승인될 때까지' : `${n}회`)
    expect(reviewCountOptions([0, 1, 2, 3, -1], 2, label)).toEqual([
      { value: '0', label: '검수 없음' },
      { value: '1', label: '1회' },
      { value: '2', label: '2회' },
      { value: '3', label: '3회' },
      { value: '-1', label: '승인될 때까지' },
    ])
    expect(reviewCountOptions([0, 1], 5, label).map((o) => o.value)).toEqual(['0', '1', '5'])
  })
})

describe('the review popover’s place (0073 T#2)', () => {
  const viewport = { width: 1000, height: 700 }
  const size = { width: 280, height: 220 }
  const m = REVIEW_POPOVER_MARGIN

  it('beside the button inside its window first (right, then left), its top on the button’s', () => {
    const button = { left: 300, top: 200, right: 400, bottom: 230 }
    expect(reviewPopoverPlace(button, size, viewport, { left: 100, top: 50, right: 900, bottom: 650 })).toEqual({ left: 406, top: 200 })
    expect(reviewPopoverPlace(button, size, viewport, { left: 0, top: 50, right: 600, bottom: 650 })).toEqual({ left: 14, top: 200 })
  })

  it('under the button (right edges aligned) when no side fits; over it when there is no room under', () => {
    const narrow = { left: 200, top: 50, right: 560, bottom: 650 }
    expect(reviewPopoverPlace({ left: 300, top: 200, right: 400, bottom: 230 }, size, viewport, narrow)).toEqual({ left: 120, top: 236 })
    expect(reviewPopoverPlace({ left: 300, top: 600, right: 400, bottom: 630 }, size, viewport, narrow)).toEqual({ left: 120, top: 374 })
  })

  it('always inside the viewport: clamped to the margins, a popover larger than the viewport at the top-left margin', () => {
    const at = reviewPopoverPlace({ left: 0, top: 690, right: 40, bottom: 700 }, size, { width: 300, height: 400 })
    expect(at.left).toBeGreaterThanOrEqual(m)
    expect(at.left + size.width).toBeLessThanOrEqual(300 - m)
    expect(at.top).toBeGreaterThanOrEqual(m)
    expect(at.top + size.height).toBeLessThanOrEqual(400 - m)
    expect(reviewPopoverPlace({ left: 10, top: 10, right: 20, bottom: 20 }, { width: 500, height: 500 }, { width: 300, height: 300 })).toEqual({ left: m, top: m })
  })

  it('a button off the viewport’s left or right (scrolled, its window dragged out) still gets a popover inside the viewport', () => {
    const plan = { left: 0, top: 0, right: 820, bottom: 640 }
    expect(reviewPopoverPlace({ left: -150, top: 200, right: -50, bottom: 230 }, size, viewport, plan)).toEqual({ left: m, top: 236 })
    expect(reviewPopoverPlace({ left: 1100, top: 200, right: 1200, bottom: 230 }, size, viewport, { left: 900, top: 0, right: 1400, bottom: 640 })).toEqual({ left: 712, top: 236 })
    expect(reviewPopoverPlace({ left: 1100, top: 200, right: 1200, bottom: 230 }, size, viewport)).toEqual({ left: 712, top: 236 })
    // every path, every horizontal position of the button: left within [m, viewport − width − m]
    for (let x = -600; x <= 1600; x += 25) {
      for (const bounds of [null, plan, { left: x - 100, top: 0, right: x + 700, bottom: 640 }]) {
        for (const y of [100, 600]) {
          const at = reviewPopoverPlace({ left: x, top: y, right: x + 100, bottom: y + 30 }, size, viewport, bounds)
          expect(at.left).toBeGreaterThanOrEqual(m)
          expect(at.left + size.width).toBeLessThanOrEqual(viewport.width - m)
          expect(at.top).toBeGreaterThanOrEqual(m)
          expect(at.top + size.height).toBeLessThanOrEqual(viewport.height - m)
        }
      }
    }
  })
})

describe('the Step window boundary (T#2)', () => {
  it('a copy of the row’s pre-instruction values (no review: 0073 T#2); locked or missing rows give none', () => {
    expect(stepDraftOf(draft(), 'c_t1|main')).toEqual({
      preInstructionText: '사전 지시',
      preInstructionAttachment: { doc_id: DOC, filename: 'r_1.txt', original_filename: 'brief.txt', content_sha256: 'a'.repeat(64) },
    })
    expect(stepDraftOf(draft(), 'c_ts1|result')).toBeNull()
    expect(stepDraftOf(draft(), 'nope|main')).toBeNull()
  })

  it('apply puts the values on that row only and leaves its review as it is', () => {
    const reviewed = setStepReview(draft(), 'c_t2|main', 2, 'aip_opus', name)
    const result = applyStepDraft(reviewed, 'c_t2|main', { preInstructionText: '새 지시', preInstructionAttachment: null })
    expect(result.status).toBe('applied')
    if (result.status !== 'applied') return
    const steps = bodyOf(result.draft).steps
    expect(steps[3]).toMatchObject({ review_count: 2, reviewer_provider_id: 'aip_opus', pre_instruction_text: '새 지시', origin: 'human' })
    expect(steps.filter((_, i) => i !== 3)).toEqual(baseSteps().filter((_, i) => i !== 3))
  })

  it('a result Step takes no pre-instruction; unchanged values give the draft back; a moved card keeps its row', () => {
    expect(applyStepDraft(draft(), 'c_t1|result', { preInstructionText: 'x', preInstructionAttachment: null }).status).toBe('not_eligible')
    const base = draft()
    expect(applyStepDraft(base, 'c_t1|result', { preInstructionText: null, preInstructionAttachment: null })).toEqual({ status: 'applied', draft: base })
    const same = applyStepDraft(base, 'c_t1|main', stepDraftOf(base, 'c_t1|main')!)
    expect(same.status === 'applied' && same.draft).toBe(base)
    const moved = moveCard(draft(), 2, 1, STARTED)
    if (moved.status !== 'moved') throw new Error('not moved')
    const applied = applyStepDraft(moved.draft, 'c_t1|main', { preInstructionText: '그대로', preInstructionAttachment: null })
    if (applied.status !== 'applied') throw new Error('not applied')
    const t1 = bodyOf(applied.draft).steps.find((s) => s.card_id === 'c_t1' && s.pair_role === 'instruction')
    expect(t1).toMatchObject({ key: 'T#2', review_count: 1, reviewer_provider_id: 'aip_sonnet', pre_instruction_text: '그대로', pre_instruction_attachment: null })
    expect(applyStepDraft(draft(), 'c_ts1|result', { preInstructionText: null, preInstructionAttachment: null }).status).toBe('locked')
    expect(applyStepDraft(draft(), 'gone|main', { preInstructionText: null, preInstructionAttachment: null }).status).toBe('gone')
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

describe('DS / T2 are not drawn; the body keeps them (0073 T#1)', () => {
  const hiddenDraft = () => draftOf(editBodyOf(hiddenPlanBody()))
  const ids = (d: WorkPlanDraft) => d.cards.map((c) => c.id)

  it('the boxes leave DS / T2 out; quantities, counted_types and Steps go back untouched', () => {
    const d = hiddenDraft()
    expect(quantityBoxes(d.body).map((b) => b.type)).toEqual(['D', 'P', 'L', 'DB', 'N', 'T', 'TS'])
    expect(bodyOf(d)).toEqual(hiddenPlanBody())
    expect(bodyOf(d).quantities).toMatchObject({ DS: { unit: 'sheet', count: 1 }, T2: { unit: 'set', count: 1 } })
    expect(bodyOf(d).counted_types).toEqual(['DS', 'D', 'P', 'L', 'DB', 'N', 'T', 'T2', 'TS'])
  })

  it('shown cards: draft.cards index ↔ shown position; rows numbered among the shown rows', () => {
    const shown = shownCardsOf(hiddenDraft())
    expect(shown.map((s) => [s.card.id, s.index, s.position])).toEqual([['c_d1', 1, 0], ['c_t1', 2, 1], ['c_t2', 4, 2], ['c_ts1', 5, 3]])
    expect(shown.flatMap((s) => s.rows.map((r) => `${r.no}:${r.step.key}`))).toEqual(['1:D#1', '2:T#1', '3:TR#1', '4:T#2', '5:TR#2', '6:TS#1', '7:TSR#1'])
    expect(shown[2].rows[0]).toMatchObject({ rowId: 'c_t2|main', cardIndex: 4, stepIndex: 0 })
    expect([...hiddenRowIds(hiddenDraft())]).toEqual(['c_ds1|main', 'c_t21|main', 'c_t21|result'])
  })

  it('the shown totals leave DS / T2 out; unassigned (the server count) keeps them, split by what is shown', () => {
    const d = hiddenDraft()
    expect(totalsOf(d)).toEqual({ designSheets: 1, workSets: 3, steps: 7 })
    // T#1, TR#2 shown; DS#1, TR2#1 hidden
    expect(unassignedSteps(d)).toBe(4)
    expect(unassignedByShown(d)).toEqual({ shown: 2, hidden: 2 })
  })

  it('a Step key of a finding: hidden types are not shown, by the Step or by the type sent', () => {
    const d = hiddenDraft()
    expect(stepKeyShown(d, 'T#1')).toBe(true)
    expect(stepKeyShown(d, 'DS#1')).toBe(false)
    expect(stepKeyShown(d, 'TR2#1')).toBe(false)
    expect(stepKeyShown(d, 'T2#9', 'T2')).toBe(false)
    expect(stepKeyShown(d, 'TR2#9', 'TR2')).toBe(false)
    expect(stepKeyShown(d, 'T#9', 'T')).toBe(true)
    expect(stepKeyShown(d, 'X#9')).toBe(true)
  })

  it('▲ of a shown card: the shown cards change places, every hidden card keeps its slot and key', () => {
    const before = hiddenDraft()
    const result = moveShownCard(before, 2, 1, STARTED)
    expect(result.status).toBe('moved')
    if (result.status !== 'moved') return
    expect(ids(result.draft)).toEqual(['c_ds1', 'c_d1', 'c_t2', 'c_t21', 'c_t1', 'c_ts1'])
    const body = bodyOf(result.draft)
    expect(body.steps.map((s) => `${s.card_id}:${s.key}>${s.pair_key ?? '-'}`)).toEqual([
      'c_ds1:DS#1>-', 'c_d1:D#1>-', 'c_t2:T#1>TR#1', 'c_t2:TR#1>T#1', 'c_t21:T2#1>TR2#1', 'c_t21:TR2#1>T2#1',
      'c_t1:T#2>TR#2', 'c_t1:TR#2>T#2', 'c_ts1:TS#1>TSR#1', 'c_ts1:TSR#1>TS#1',
    ])
    expect(hiddenSteps(body)).toEqual(hiddenSteps(hiddenPlanBody()))
    expect(body.quantities).toEqual(hiddenPlanBody().quantities)
    expect(body.counted_types).toEqual(hiddenPlanBody().counted_types)
    // a drop at the end: shown position 1 → last
    const end = moveShownCard(before, 1, 3, STARTED)
    if (end.status !== 'moved') throw new Error('not moved')
    expect(ids(end.draft)).toEqual(['c_ds1', 'c_d1', 'c_t2', 'c_t21', 'c_ts1', 'c_t1'])
    expect(hiddenSteps(bodyOf(end.draft))).toEqual(hiddenSteps(hiddenPlanBody()))
  })

  it('started cards stay pinned among the shown cards, a hidden started card too', () => {
    const d = hiddenDraft()
    expect(moveShownCard(d, 0, 2, STARTED).status).toBe('started')
    expect(moveShownCard(d, 1, 0, STARTED).status).toBe('before_started')
    expect(canMoveShownCardBy(d, 1, -1, STARTED)).toBe(false)
    expect(canMoveShownCardBy(d, 1, 1, STARTED)).toBe(true)
    expect(canMoveShownCardBy(d, 3, 1, STARTED)).toBe(false)
    expect(canMoveShownCardBy(d, 4, -1, STARTED)).toBe(false)
    // the hidden T2 card started: nothing may land in front of it, nor pass it
    const t2Started = new Set(['c_d1', 'c_t21'])
    expect(moveShownCard(d, 2, 1, t2Started).status).toBe('before_started')
    expect(moveShownCard(d, 1, 2, t2Started).status).toBe('before_started')
    expect(canMoveShownCardBy(d, 1, 1, t2Started)).toBe(false)
    const after = moveShownCard(d, 3, 2, t2Started)
    if (after.status !== 'moved') throw new Error('not moved')
    expect(ids(after.draft)).toEqual(['c_ds1', 'c_d1', 'c_t1', 'c_t21', 'c_ts1', 'c_t2'])
  })

  it('with no hidden card a shown move is the plain card move', () => {
    const moves: Array<[number, number]> = [[2, 1], [1, 3], [3, 1], [1, 0], [0, 2], [1, 1]]
    for (const [from, to] of moves) {
      expect(moveShownCard(draft(), from, to, STARTED)).toEqual(moveCard(draft(), from, to, STARTED))
    }
    for (let index = 0; index < 4; index++) {
      for (const delta of [-1, 1]) expect(canMoveShownCardBy(draft(), index, delta, STARTED)).toBe(canMoveCardBy(draft(), index, delta, STARTED))
    }
  })

  it('[apply to all] still reaches the unlocked hidden Steps (scenario B); review, pre-instruction, x_* stay', () => {
    const d = applyDefaults(hiddenDraft(), name)
    const steps = bodyOf(d).steps
    for (const s of steps.filter((item) => !item.locked)) expect(s).toMatchObject({ provider_id: 'aip_opus', provider_display_name: 'Opus', note: '기본 메모', origin: 'human' })
    const hidden = hiddenSteps(bodyOf(d))
    expect(hidden.map((s) => s.key)).toEqual(['DS#1', 'T2#1', 'TR2#1'])
    expect(hidden[0]).toMatchObject({ provider_id: 'aip_opus', note: '기본 메모', x_hidden: { kept: true } })
    expect(hidden[1]).toMatchObject({ provider_id: 'aip_opus', review_count: 2, reviewer_provider_id: 'aip_opus', pre_instruction_text: '반영 사전 지시' })
    expect(steps.find((s) => s.key === 'TSR#1')).toEqual((hiddenPlanBody().steps as Array<Record<string, unknown>>).find((s) => s.key === 'TSR#1'))
    expect(bodyOf(d).quantities).toEqual(hiddenPlanBody().quantities)
  })

  it('the icon of a type is the Palette item’s; a result is `file` in its instruction’s accent', () => {
    const palette = (docType: string) => DEFAULT_PALETTE_ITEMS.find((item) => item.docType === docType)!
    expect(workPlanTypeIcon('D')).toEqual({ icon: 'monitor', accent: palette('D').accent })
    expect(workPlanTypeIcon('P')).toEqual({ icon: 'flow', accent: palette('P').accent })
    expect(workPlanTypeIcon('N')).toEqual({ icon: 'search', accent: palette('N').accent })
    expect(workPlanTypeIcon('T')).toEqual({ icon: 'pencil', accent: palette('T').accent })
    expect(workPlanTypeIcon('TS')).toEqual({ icon: 'test', accent: palette('TS').accent })
    expect(workPlanTypeIcon('TR')).toEqual({ icon: 'file', accent: palette('T').accent })
    expect(workPlanTypeIcon('NR')).toEqual({ icon: 'file', accent: palette('N').accent })
    expect(workPlanTypeIcon('TSR')).toEqual({ icon: 'file', accent: palette('TS').accent })
    expect(workPlanTypeIcon('X')).toEqual({ icon: 'document', accent: null })
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
