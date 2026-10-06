// flowgate.default.0649 T#3 (NR0003 §5.3 / O7 / §11 client) — the work-plan editor works in
// CARDS: a single step, or a set's instruction + result pair. Covers
//   * moving a card (▲▼ and HTML5 drag) renumbers keys/ordinals by order while card_id and
//     every value travel with the card; the PUT body keeps that order and those ids;
//   * a same-type overtake keeps the open drawer and the per-row errors on their own cards;
//   * quantity changes keep the person's order, a new card is appended at the end and sent
//     without card_id, and a
//     card removed and re-added in the same session comes back with its card_id and values;
//   * a started card (step_execution_status[].started) cannot move, cannot be removed by a
//     quantity decrease, and nothing can be put in front of it; without a sequence every card
//     moves freely;
//   * import/export keep card_id and the mixed order; a refused import shows card_id_missing.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import WorkPlanEditor from '@main/components/WorkPlanEditor.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest, putRequest, showToast, dialogConfirm } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
  showToast: vi.fn(),
  dialogConfirm: vi.fn(() => Promise.resolve(true)),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  getRequest,
  postRequest,
  putRequest,
  patchRequest: vi.fn(),
  postFormRequest: vi.fn(),
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))
vi.mock('@main/composables/useDialogStack', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  confirm: dialogConfirm,
}))

const TYPES = [
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { code: 'N', label: '조사지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'NR', sort_order: 2 },
  { code: 'NR', label: '조사레포트', category: 'work', countable: false },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 3 },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false },
]
const TYPES_WP = [
  { code: 'D', label: '기본설계', category: 'design', unit: 'sheet' },
  { code: 'N', label: '조사지시', category: 'instruction', unit: 'set', pair_code: 'NR' },
  { code: 'T', label: '작업지시', category: 'instruction', unit: 'set', pair_code: 'TR' },
]
const PROVIDERS = [{ id: 'aip_opus', name: 'Claude Opus', group_label: 'Claude · CLI' }]
const DOC_ID = 'flowgate.default.0649.0004-WP'

function step(
  key: string, type: string, ordinal: number, pairKey: string | null,
  role: 'single' | 'instruction' | 'result', cardId: string | undefined, extra: Record<string, unknown> = {},
) {
  return {
    key, ...(cardId === undefined ? {} : { card_id: cardId }), type, ordinal, pair_key: pairKey, pair_role: role,
    provider_id: null, provider_display_name: null, note: null,
    review_count: 0, reviewer_provider_id: null, reviewer_provider_display_name: null,
    pre_instruction_text: null, pre_instruction_attachment: null,
    locked: false, locked_reason: null, origin: 'human', ...extra,
  }
}

/** Mixed order: T(c_a) · D(c_d) · T(c_b) — each card with its own values. */
function mixedBody() {
  return {
    wp_version: 2,
    binding: 'advisory',
    counted_types: ['D', 'N', 'T'],
    quantities: { D: { unit: 'sheet', count: 1 }, N: { unit: 'set', count: 0 }, T: { unit: 'set', count: 2 } },
    provider_candidates: [],
    defaults: { provider_id: null, note: '' },
    steps: [
      step('T#1', 'T', 1, 'TR#1', 'instruction', 'c_a', { note: 'A', pre_instruction_text: 'brief A' }),
      step('TR#1', 'TR', 1, 'T#1', 'result', 'c_a', { note: 'report A' }),
      step('D#1', 'D', 1, null, 'single', 'c_d', { note: 'D' }),
      step('T#2', 'T', 2, 'TR#2', 'instruction', 'c_b', { note: 'B', pre_instruction_text: 'brief B', review_count: 2 }),
      step('TR#2', 'TR', 2, 'T#2', 'result', 'c_b', { note: 'report B' }),
    ],
  }
}

type Body = ReturnType<typeof mixedBody>

function readResponse(body: Body, started: string[] = []) {
  return {
    ok: true, doc_id: DOC_ID, doc_type: 'WP', title: '0649 작업계획',
    group_id: 'flowgate.default.0649', parent_doc_id: 'flowgate.default.0649.0001-R',
    status: 'open', doc_review_status: 'pending_review', editable: true, edit_locked_reason: null,
    revision_no: 5, origin: 'human', updated_by: 'sjm', updated_at: '2026-10-07T03:00:00+09:00',
    body,
    registered_providers: PROVIDERS, provider_status: [],
    step_execution_status: body.steps.map((s: any) => ({
      step_key: s.key, card_id: s.card_id ?? null, started: started.includes(s.card_id),
      reviewer_provider: { provider_id: null, enabled: true, current_name: null },
      pre_instruction_attachment: null,
    })),
    review_count_choices: [0, 1, 2, -1],
    assignment_summary: [], unassigned_step_count: 0,
    totals: { design_sheets: 1, work_sets: 2, steps: body.steps.length },
    limits: { note_max_chars: 1000 },
    last_application: null,
  }
}

function routeGet(body: Body = mixedBody(), started: string[] = []) {
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/document-types')) return Promise.resolve({ data: { data: TYPES, work_plan_countable_types: TYPES_WP } })
    if (url.includes('/ai-invoke/providers')) return Promise.resolve({ data: { providers: structuredClone(PROVIDERS), default_provider_id: 'aip_opus' } })
    if (url.includes('/work-plan')) return Promise.resolve({ data: structuredClone(readResponse(body, started)) })
    return Promise.reject(new Error(`unexpected url: ${url}`))
  })
}

function mountEditor() {
  return mount(WorkPlanEditor, {
    props: { docId: DOC_ID, projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

type W = ReturnType<typeof mountEditor>

async function flushAll(times = 6) {
  for (let i = 0; i < times; i++) await flushPromises()
}

function cardsOf(wrapper: W) {
  return wrapper.findAll('[data-test="wp-card"]')
}

/** [type, note] per row, in screen order — the note travels with its card. */
function rowsOf(wrapper: W): Array<[string, string]> {
  return wrapper.findAll('.wp-step-row').map((row) => [
    row.find('.doc-tag').text(),
    (row.find('.wp-step-msg').element as HTMLInputElement).value,
  ])
}

function qtyButton(wrapper: W, code: string, sign: '+' | '−') {
  const card = wrapper.findAll('.wp-qty-card').find((c) => c.find('.wp-qty-tags .doc-tag').text() === code)!
  return card.findAll('.wp-stepper-btn').find((b) => b.text() === sign)!
}

function saveButton(wrapper: W) {
  return wrapper.findAll('.card-hd .card-actions button').find((b) => b.text().includes('저장'))!
}

function savedBody(callIndex = 0): Body {
  return putRequest.mock.calls[callIndex][1].body
}

function idsAndKeys(body: Body) {
  return body.steps.map((s: any) => [s.key, s.card_id ?? null, s.pair_key])
}

function echoSave(mutate?: (body: any) => void) {
  putRequest.mockImplementation((_url: string, payload: any) => {
    const body = structuredClone(payload.body)
    mutate?.(body)
    return Promise.resolve({
      data: { revision_no: 6, body, totals: { design_sheets: 1, work_sets: 2, steps: body.steps.length }, assignment_summary: [], unassigned_step_count: 0 },
    })
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  useProjectStore().currentProjectId = 'flowgate'
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  showToast.mockReset()
  dialogConfirm.mockReset()
  dialogConfirm.mockImplementation(() => Promise.resolve(true))
  routeGet()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('WorkPlanEditor cards — move and renumber (0649 T#3)', () => {
  it('draws one frame per card: a set card holds its instruction and result rows', async () => {
    const wrapper = mountEditor()
    await flushAll()
    const cards = cardsOf(wrapper)
    expect(cards.map((c) => c.attributes('data-card-type'))).toEqual(['T', 'D', 'T'])
    expect(cards.map((c) => c.findAll('.wp-step-row').length)).toEqual([2, 1, 2])
  })

  it('▲ moves a whole set card; keys/ordinals are renumbered, card_id and values stay with the card', async () => {
    echoSave()
    const wrapper = mountEditor()
    await flushAll()

    await cardsOf(wrapper)[2].get('[data-test="card-move-up"]').trigger('click') // c_b over D
    await cardsOf(wrapper)[1].get('[data-test="card-move-up"]').trigger('click') // c_b over c_a
    await flushAll()
    expect(rowsOf(wrapper)).toEqual([['T', 'B'], ['TR', 'report B'], ['T', 'A'], ['TR', 'report A'], ['D', 'D']])

    await saveButton(wrapper).trigger('click')
    await flushAll()
    const body = savedBody()
    expect(idsAndKeys(body)).toEqual([
      ['T#1', 'c_b', 'TR#1'], ['TR#1', 'c_b', 'T#1'], ['T#2', 'c_a', 'TR#2'], ['TR#2', 'c_a', 'T#2'], ['D#1', 'c_d', null],
    ])
    expect(body.steps.map((s: any) => s.ordinal)).toEqual([1, 1, 2, 2, 1])
    // provider/note/review/pre-instruction moved with the card, not with the key
    expect(body.steps[0]).toMatchObject({ note: 'B', review_count: 2, pre_instruction_text: 'brief B' })
    expect(body.steps[2]).toMatchObject({ note: 'A', review_count: 0, pre_instruction_text: 'brief A' })
    expect(body.quantities).toEqual(mixedBody().quantities)
  })

  it('drag-and-drop puts the dragged card in front of the drop target', async () => {
    const wrapper = mountEditor()
    await flushAll()
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' }
    await cardsOf(wrapper)[1].get('[data-test="card-drag-handle"]').trigger('dragstart', { dataTransfer })
    expect(wrapper.find('[data-test="card-drop-end"]').exists()).toBe(true)
    await cardsOf(wrapper)[0].trigger('dragover')
    await cardsOf(wrapper)[0].trigger('drop')
    await flushAll()
    expect(cardsOf(wrapper).map((c) => c.attributes('data-card-type'))).toEqual(['D', 'T', 'T'])
    expect(rowsOf(wrapper)[0]).toEqual(['D', 'D'])

    // and the end zone moves a card to the very end
    await cardsOf(wrapper)[0].get('[data-test="card-drag-handle"]').trigger('dragstart', { dataTransfer })
    await wrapper.get('[data-test="card-drop-end"]').trigger('drop')
    await flushAll()
    expect(cardsOf(wrapper).map((c) => c.attributes('data-card-type'))).toEqual(['T', 'T', 'D'])
    expect(wrapper.find('[data-test="card-drop-end"]').exists()).toBe(false)
  })

  it('a same-type overtake keeps the open drawer and the row errors on their own cards', async () => {
    putRequest.mockRejectedValueOnce({
      response: {
        status: 422,
        data: {
          message: '검증 실패',
          errors: [{ loc: 'steps[0].note', key: 'T#1', code: 'note_too_long', params: { max: 1000 }, msg: 'too long' }],
        },
      },
    })
    const wrapper = mountEditor()
    await flushAll()
    // open c_b's drawer (T#2), then a save fails with an error on T#1 (= c_a)
    await cardsOf(wrapper)[2].findAll('[data-test="step-name-toggle"]')[0].trigger('click')
    await saveButton(wrapper).trigger('click')
    await flushAll()
    expect(cardsOf(wrapper)[0].find('.wp-step-errors').exists()).toBe(true)

    // c_b overtakes c_a: it becomes T#1, c_a becomes T#2
    await cardsOf(wrapper)[2].get('[data-test="card-move-up"]').trigger('click')
    await cardsOf(wrapper)[1].get('[data-test="card-move-up"]').trigger('click')
    await flushAll()

    const [first, second] = cardsOf(wrapper)
    expect((first.get('[data-test="drawer-instr-text"]').element as HTMLTextAreaElement).value).toBe('brief B')
    expect(first.find('.wp-step-errors').exists()).toBe(false)
    expect(second.find('[data-test="step-drawer"]').exists()).toBe(false)
    expect(second.find('.wp-step-errors').exists()).toBe(true)
    expect((second.find('.wp-row-error .wp-step-msg').element as HTMLInputElement).value).toBe('A')
  })

  it('without a poured sequence every card can move (no started card)', async () => {
    const wrapper = mountEditor()
    await flushAll()
    const cards = cardsOf(wrapper)
    expect(cards.every((c) => c.get('[data-test="card-drag-handle"]').attributes('draggable') === 'true')).toBe(true)
    expect(cards[0].get('[data-test="card-move-up"]').attributes('disabled')).toBeDefined() // top edge only
    expect(cards[1].get('[data-test="card-move-up"]').attributes('disabled')).toBeUndefined()
    expect(cards[2].get('[data-test="card-move-down"]').attributes('disabled')).toBeDefined() // bottom edge only
  })
})

describe('WorkPlanEditor cards — quantities keep the order (0649 T#3)', () => {
  it('+ appends a card at the end of the current order, without card_id; the others keep keys and order', async () => {
    echoSave()
    const wrapper = mountEditor()
    await flushAll()
    await qtyButton(wrapper, 'T', '+').trigger('click')
    await flushAll()
    expect(cardsOf(wrapper).map((c) => c.attributes('data-card-type'))).toEqual(['T', 'D', 'T', 'T'])

    await saveButton(wrapper).trigger('click')
    await flushAll()
    expect(idsAndKeys(savedBody())).toEqual([
      ['T#1', 'c_a', 'TR#1'], ['TR#1', 'c_a', 'T#1'], ['D#1', 'c_d', null],
      ['T#2', 'c_b', 'TR#2'], ['TR#2', 'c_b', 'T#2'], ['T#3', null, 'TR#3'], ['TR#3', null, 'T#3'],
    ])
    expect(savedBody().steps.slice(5).every((s: any) => !('card_id' in s))).toBe(true)
    expect(savedBody().quantities.T.count).toBe(3)
  })

  it('in a mixed plan a new card goes to the end, not next to the cards of its type (NR0003 §5.3)', async () => {
    echoSave()
    const wrapper = mountEditor()
    await flushAll()
    await qtyButton(wrapper, 'D', '+').trigger('click')
    await flushAll()
    // D#1 sits between the two T cards; the new D is appended after the last card, not after D#1
    expect(cardsOf(wrapper).map((c) => c.attributes('data-card-type'))).toEqual(['T', 'D', 'T', 'D'])

    await saveButton(wrapper).trigger('click')
    await flushAll()
    expect(idsAndKeys(savedBody())).toEqual([
      ['T#1', 'c_a', 'TR#1'], ['TR#1', 'c_a', 'T#1'], ['D#1', 'c_d', null],
      ['T#2', 'c_b', 'TR#2'], ['TR#2', 'c_b', 'T#2'], ['D#2', null, null],
    ])
    expect('card_id' in savedBody().steps[5]).toBe(false)
  })

  it('a type with no card yet is appended at the end as well', async () => {
    const wrapper = mountEditor()
    await flushAll()
    await qtyButton(wrapper, 'N', '+').trigger('click')
    await flushAll()
    expect(cardsOf(wrapper).map((c) => c.attributes('data-card-type'))).toEqual(['T', 'D', 'T', 'N'])
  })

  it('− removes the last card of the type (confirming values); + in the same session restores its card_id and values', async () => {
    echoSave()
    const wrapper = mountEditor()
    await flushAll()
    await qtyButton(wrapper, 'T', '−').trigger('click')
    await flushAll()
    expect(dialogConfirm).toHaveBeenCalledTimes(1) // c_b carries note/review/pre-instruction
    expect(rowsOf(wrapper).map(([, note]) => note)).toEqual(['A', 'report A', 'D'])

    await qtyButton(wrapper, 'T', '+').trigger('click')
    await flushAll()
    expect(rowsOf(wrapper)).toEqual([['T', 'A'], ['TR', 'report A'], ['D', 'D'], ['T', 'B'], ['TR', 'report B']])

    await saveButton(wrapper).trigger('click')
    await flushAll()
    expect(idsAndKeys(savedBody())).toEqual(idsAndKeys(mixedBody()))
    expect(savedBody().steps[3]).toMatchObject({ card_id: 'c_b', note: 'B', review_count: 2, pre_instruction_text: 'brief B' })
  })

  it('cancelling the removal confirmation leaves the plan untouched', async () => {
    dialogConfirm.mockImplementation(() => Promise.resolve(false))
    const wrapper = mountEditor()
    await flushAll()
    await qtyButton(wrapper, 'T', '−').trigger('click')
    await flushAll()
    expect(cardsOf(wrapper)).toHaveLength(3)
    expect(wrapper.find('.wp-dirty-banner').exists()).toBe(false)
  })

  it('a new card saved by the server gets its card_id, and an open drawer stays on that card', async () => {
    echoSave((body) => {
      for (const s of body.steps) if (!s.card_id) s.card_id = 'c_new'
    })
    const wrapper = mountEditor()
    await flushAll()
    await qtyButton(wrapper, 'T', '+').trigger('click')
    await flushAll()
    await cardsOf(wrapper)[3].findAll('[data-test="step-name-toggle"]')[0].trigger('click')
    await saveButton(wrapper).trigger('click')
    await flushAll()

    expect(cardsOf(wrapper)[3].find('[data-test="step-drawer"]').exists()).toBe(true)
    // the server-named card moves like any other card afterwards
    await cardsOf(wrapper)[3].get('[data-test="card-move-up"]').trigger('click')
    await flushAll()
    expect(cardsOf(wrapper)[2].find('[data-test="step-drawer"]').exists()).toBe(true)
  })
})

describe('WorkPlanEditor cards — started cards are pinned (0649 T#3, NR0003 O7)', () => {
  it('a started card has no drag handle and no ▲▼; no card can be put in front of it', async () => {
    routeGet(mixedBody(), ['c_a'])
    const wrapper = mountEditor()
    await flushAll()
    const [a, d, b] = cardsOf(wrapper)
    expect(a.classes()).toContain('is-started')
    const lock = a.get('[data-test="card-started-lock"]')
    expect(lock.attributes('draggable')).toBe('false')
    expect(a.get('[data-test="card-move-up"]').attributes('disabled')).toBeDefined()
    expect(a.get('[data-test="card-move-down"]').attributes('disabled')).toBeDefined()
    // D right behind the started card cannot go up; B can go up (behind A) but not further
    expect(d.get('[data-test="card-move-up"]').attributes('disabled')).toBeDefined()
    expect(b.get('[data-test="card-move-up"]').attributes('disabled')).toBeUndefined()
    expect(d.get('[data-test="card-drag-handle"]').attributes('draggable')).toBe('true')
  })

  it('dropping a card in front of a started card is refused', async () => {
    routeGet(mixedBody(), ['c_a'])
    const wrapper = mountEditor()
    await flushAll()
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' }
    await cardsOf(wrapper)[2].get('[data-test="card-drag-handle"]').trigger('dragstart', { dataTransfer })
    await cardsOf(wrapper)[0].trigger('drop')
    await flushAll()
    expect(cardsOf(wrapper).map((c) => c.attributes('data-card-type'))).toEqual(['T', 'D', 'T'])
    expect(rowsOf(wrapper)[0]).toEqual(['T', 'A'])
    expect(showToast).toHaveBeenCalledWith(i18n.global.t('main.work_plan.card_move_before_started'), 'warning')
    expect(wrapper.find('.wp-dirty-banner').exists()).toBe(false)
  })

  it('a quantity decrease skips the started card and refuses once only started cards remain', async () => {
    routeGet(mixedBody(), ['c_a'])
    const wrapper = mountEditor()
    await flushAll()
    await qtyButton(wrapper, 'T', '−').trigger('click')
    await flushAll()
    expect(rowsOf(wrapper).map(([, note]) => note)).toEqual(['A', 'report A', 'D'])

    await qtyButton(wrapper, 'T', '−').trigger('click')
    await flushAll()
    expect(rowsOf(wrapper).map(([, note]) => note)).toEqual(['A', 'report A', 'D'])
    expect(wrapper.find('.wp-qty-card .wp-qty-value').exists()).toBe(true)
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.work_plan.quantity_decrease_blocked_started', { type: 'T' }), 'warning',
    )
  })

  it('a stale body with cards in front of a started card lets them move only behind it', async () => {
    routeGet(mixedBody(), ['c_b'])
    const wrapper = mountEditor()
    await flushAll()
    const [a, d, b] = cardsOf(wrapper)
    // A one step down would still land in front of the started c_b; D one step down lands behind it
    expect(a.get('[data-test="card-move-down"]').attributes('disabled')).toBeDefined()
    expect(d.get('[data-test="card-move-down"]').attributes('disabled')).toBeUndefined()
    expect(b.get('[data-test="card-started-lock"]').attributes('draggable')).toBe('false')
    // dragging behind the started card is allowed and repairs the order
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' }
    await a.get('[data-test="card-drag-handle"]').trigger('dragstart', { dataTransfer })
    await wrapper.get('[data-test="card-drop-end"]').trigger('drop')
    await flushAll()
    expect(rowsOf(wrapper).map(([, note]) => note)).toEqual(['D', 'B', 'report B', 'A', 'report A'])
  })
})

describe('WorkPlanEditor cards — import/export (0649 T#3)', () => {
  it('export ships the server body with card_id and the mixed order', async () => {
    const createdParts: string[] = []
    vi.stubGlobal('Blob', class {
      constructor(parts: string[]) { createdParts.push(parts[0]) }
    } as any)
    vi.stubGlobal('URL', { createObjectURL: vi.fn().mockReturnValue('blob:x'), revokeObjectURL: vi.fn() })
    const realCreateElement = document.createElement.bind(document)
    const anchor = realCreateElement('a')
    anchor.click = vi.fn()
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => (tag === 'a' ? anchor : realCreateElement(tag)))
    const wrapper = mountEditor()
    await flushAll()
    await wrapper.findAll('.card-hd .card-actions button').find((b) => b.text().includes('다운로드'))!.trigger('click')
    await flushAll()
    const exported = JSON.parse(createdParts[0])
    expect(idsAndKeys(exported)).toEqual(idsAndKeys(mixedBody()))
    vi.unstubAllGlobals()
  })

  it('a reordered file without card_id is refused by the server and card_id_missing is shown', async () => {
    putRequest.mockRejectedValue({
      response: {
        status: 422,
        data: {
          message: '검증 실패',
          errors: [{ loc: 'steps[0].card_id', key: 'T#1', code: 'card_id_missing', params: {}, msg: 'missing' }],
        },
      },
    })
    const wrapper = mountEditor()
    await flushAll()
    const uploaded = mixedBody()
    for (const s of uploaded.steps as any[]) delete s.card_id
    const file = new File([JSON.stringify(uploaded)], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    expect(putRequest.mock.calls[0][1].body).toEqual(uploaded) // sent as-is: the server decides
    expect(wrapper.text()).toContain(i18n.global.t('main.work_plan.errors.card_id_missing'))
    expect(rowsOf(wrapper)[0]).toEqual(['T', 'A']) // the screen keeps the stored plan
  })
})
