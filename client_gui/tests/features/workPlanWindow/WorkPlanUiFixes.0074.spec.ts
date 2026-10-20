/** T0017: reversible instruction configuration, preserved document data and design instruction access.
 * Uses the real document windows with mocked work-plan reads and writes.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DocumentWindows, closeAllDocumentAiWindows } from '@/features/documentWindow'
import { closeAllWorkPlanEditWindows, openWorkPlanEditWindow, setWorkPlanStepEditHandler } from '@/features/workPlanWindow'
import { WORK_PLAN_OMIT_TEXT, omitEligible } from '@/features/workPlanWindow/workPlanEditModel'
import { DOC, FLOW, hiddenPlanBody, planBody, readAnswer, step } from './workPlanFixtures'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), putRequest: vi.fn(), postRequest: vi.fn() }))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, ...api }
})

registerAppMessages(i18n)

const WP_PATH = `/api/v1/documents/${encodeURIComponent(DOC)}/work-plan`
const DEPLOY = `${WP_PATH}/deploy`
const PREVIEW = `${DEPLOY}/preview`

let answer: Record<string, unknown>
let deployStatus: Record<string, unknown> | null
let putAnswer: (payload: any) => unknown
let previewAnswer: (body: any) => unknown
let executeAnswer: (body: any) => unknown
let previews = 0
let wrapper: VueWrapper<any> | null = null

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

function savedAnswer(payload: { base_revision_no: number; body: Record<string, any> }): Record<string, unknown> {
  const body = JSON.parse(JSON.stringify(payload.body))
  for (const s of body.steps) if (!s.card_id) s.card_id = `c_new_${s.pair_role === 'result' ? s.pair_key : s.key}`
  return { ok: true, doc_id: DOC, revision_no: payload.base_revision_no + 1, title: '작업계획', doc_review_status: 'pending_review', body, warnings: [] }
}

function statusAnswer(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true, doc_id: DOC, flow_id: FLOW, wp_node_key: 'gn_wp', ownership: 'graph', wp_revision_no: 2, deployable: true, reason: null,
    deployed_count: 0, target_count: 4, missing_count: 4, orphan_count: 0, mismatch_count: 0, cards: [], modes: { first: true, add: false, cleanup: false },
    ...over,
  }
}

function previewOf(body: any, over: Record<string, unknown> = {}): Record<string, unknown> {
  previews += 1
  return {
    ok: true, doc_id: DOC, mode: body.mode, cards: [], blockers: [], writes: true, no_change: false, executable: true,
    counts: {}, baseline: { wp_revision_no: body.wp_revision_no, plan_digest: `digest-${previews}`, draft_version: 40 + previews, base_revision_no: 1, remove_node_keys: [] },
    ...over,
  }
}

const deployCard = (cardId: string, over: Record<string, unknown> = {}) => ({
  card_id: cardId, doc_type: 'T', node_key: `gn_${cardId}`, result: 'already_deployed', reason: null, instruction: 'instruction_write', instruction_reason: null, settings_skipped: [], ...over,
})

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllDocumentAiWindows()
  closeAllWorkPlanEditWindows()
  setWorkPlanStepEditHandler(null)
  for (const fn of Object.values(api)) fn.mockReset()
  answer = readAnswer()
  deployStatus = statusAnswer()
  previews = 0
  putAnswer = (payload) => savedAnswer(payload)
  previewAnswer = (body) => previewOf(body)
  executeAnswer = (body) => ({ ok: true, doc_id: DOC, mode: body.mode, cards: [deployCard('c_t2'), deployCard('c_d1', { doc_type: 'D' })], no_change: false, draft_version: 50, created_node_keys: [], removed_node_keys: [], key_map: {}, counts: {} })
  api.getRequest.mockImplementation(async (path: string) => {
    if (path === WP_PATH) return { data: JSON.parse(JSON.stringify(answer)) }
    if (path === DEPLOY) {
      if (deployStatus === null) throw axiosError(500, { ok: false, code: 'internal_error' })
      return { data: JSON.parse(JSON.stringify(deployStatus)) }
    }
    if (path === '/api/v1/ai-invoke/active') return { data: { active: false } }
    throw new Error(`unexpected GET ${path}`)
  })
  api.putRequest.mockImplementation(async (_path: string, payload: any) => ({ data: await putAnswer(payload) }))
  api.postRequest.mockImplementation(async (path: string, body: any) => {
    if (path === PREVIEW) return { data: await previewAnswer(body) }
    if (path === DEPLOY) return { data: await executeAnswer(body) }
    throw new Error(`unexpected POST ${path}`)
  })
  wrapper = mount(DocumentWindows, { global: { plugins: [i18n] }, attachTo: document.body })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  closeAllWorkPlanEditWindows()
  closeAllDocumentAiWindows()
  setWorkPlanStepEditHandler(null)
  document.body.innerHTML = ''
})

const q = (id: string, root: ParentNode = document.body) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
const text = (node: Element | null | undefined) => node?.textContent?.replace(/\s+/g, ' ').trim() ?? null
const row = (rowId: string) => document.body.querySelector<HTMLElement>(`[data-test="wp-edit-step"][data-row="${rowId}"]`)
const omit = (rowId: string) => q('wp-edit-step-omit', row(rowId)!) as HTMLButtonElement | null
const mark = (rowId: string) => q('wp-edit-step-instruction', row(rowId)!)
const puts = () => api.putRequest.mock.calls.map(([, payload]) => payload as { base_revision_no: number; body: Record<string, any> })
const postsTo = (path: string) => api.postRequest.mock.calls.filter(([p]) => p === path).map(([, body]) => body as Record<string, unknown>)
const statusReads = () => api.getRequest.mock.calls.filter(([p]) => p === DEPLOY).length
const sentStep = (payload: { body: Record<string, any> }, key: string) => payload.body.steps.find((s: any) => s.key === key)
const notice = () => q('wp-edit-notice')

async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}

async function open(extra: Partial<Parameters<typeof openWorkPlanEditWindow>[0]> = {}) {
  openWorkPlanEditWindow({ docId: DOC, projectId: 'flowgate', title: '작업계획', ...extra })
  await flushPromises()
}


const card = (id: string) => document.querySelector<HTMLElement>(`[data-test="wp-edit-card"][data-card="${id}"]`)!
const mode = (id: string) => q('wp-edit-instruction-mode', card(id)) as HTMLInputElement
async function toggle(id: string, on: boolean) {
  const input = mode(id)
  input.checked = on
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
}

describe('T0017 ordinary instruction configuration', () => {
  it('ON/OFF is a saved reversible choice, preserving content, attachments, IDs and report settings', async () => {
    const original = planBody() as any
    answer = readAnswer({ body: original, step_execution_status: [] })
    putAnswer = (payload) => {
      const saved = savedAnswer(payload)
      answer = { ...answer, ...saved }
      return saved
    }
    await open()
    expect(mode('c_t1').checked).toBe(false)
    await toggle('c_t1', true)
    expect(row('c_t1|main')).toBeNull()
    expect(row('c_t1|result')).not.toBeNull()
    expect(text(q('wp-edit-step-totals'))).toContain('7')
    expect(api.putRequest).not.toHaveBeenCalled()
    expect(api.postRequest).not.toHaveBeenCalled()
    await click(q('wp-edit-save'))
    const saved = puts()[0].body.steps
    expect(saved.find((s: any) => s.key === 'T#1')).toEqual({ ...original.steps[1], instruction_mode: 'omit' })
    expect(saved.find((s: any) => s.key === 'TR#1')).toEqual(original.steps[2])
    closeAllWorkPlanEditWindows()
    await flushPromises()
    await open()
    expect(mode('c_t1').checked).toBe(true)
    expect(row('c_t1|main')).toBeNull()
    await toggle('c_t1', false)
    expect(row('c_t1|main')).not.toBeNull()
    expect(mark('c_t1|main')).not.toBeNull()
    await click(q('wp-edit-save'))
    expect(sentStep(puts()[1], 'T#1')).toEqual({ ...original.steps[1], instruction_mode: 'use' })
    expect(api.postRequest).not.toHaveBeenCalled()
  })

  it('new unsaved cards can configure omission before their first save', async () => {
    await open()
    const box = document.querySelector('[data-test="wp-edit-quantity"][data-type="N"]')!
    await click(q('wp-edit-quantity-inc', box))
    const added = [...document.querySelectorAll<HTMLElement>('[data-test="wp-edit-card"]')].at(-1)!
    const id = added.dataset.card!
    await toggle(id, true)
    expect(added.querySelectorAll('[data-test="wp-edit-step"]')).toHaveLength(1)
    await click(q('wp-edit-save'))
    const instruction = puts()[0].body.steps.find((s: any) => s.type === 'N')
    expect(instruction.instruction_mode).toBe('omit')
    expect(instruction.pre_instruction_text).toBeNull()
  })

  it('started and read-only cards do not allow omission edits', async () => {
    answer = readAnswer({ step_execution_status: [{ card_id: 'c_t1', step_key: 'T#1', started: true }] })
    await open()
    expect(mode('c_t1').disabled).toBe(true)
    await toggle('c_t1', true)
    expect(row('c_t1|main')).not.toBeNull()
    closeAllWorkPlanEditWindows()
    await flushPromises()
    await open({ external: () => 'read_only' })
    expect(mode('c_t2').disabled).toBe(true)
  })

  it('a save the server refuses because the card started meanwhile names the card and keeps the edit', async () => {
    // The read said not started; the card ran in the Graph before the save (server: instruction_mode_started).
    putAnswer = () => {
      throw axiosError(422, {
        code: 'wp_validation_failed', message: '저장할 수 없습니다.',
        errors: [{ loc: 'steps[2].instruction_mode', key: 'T#1', code: 'instruction_mode_started', params: { value: 'c_t1', field: 'steps[2].instruction_mode' }, msg: 'server text' }],
      })
    }
    await open()
    await toggle('c_t1', true)
    await click(q('wp-edit-save'))
    expect(puts()).toHaveLength(1)
    // The omitted instruction row is not drawn, so its error is listed on top.
    const [shown] = [...document.querySelectorAll('[data-test="wp-edit-hidden-error"]')].map(text)
    expect(shown).toContain('이미 실행을 시작한 카드(card_id c_t1)는 지시서 생략 설정을 바꿀 수 없습니다.')
    expect(mode('c_t1').checked).toBe(true)
    expect(api.postRequest).not.toHaveBeenCalled()
  })

  it('a design instruction is a separate row and opens its preserved pre-instruction in the DS editor', async () => {
    const body = planBody() as any
    body.steps[0].pre_instruction_text = '기존 설계지시'
    body.steps[0].pre_instruction_attachment = { original_filename: 'design.txt' }
    answer = readAnswer({ body })
    await open()
    const design = card('c_d1')
    expect(text(design)).toContain('설계 지시')
    expect(text(design)).toContain('설계서')
    await click(q('wp-edit-card-toggle', design))
    await click(q('wp-edit-step-edit', row('c_d1|main')!))
    expect(text(q('wp-step-heading'))).toContain('설계 지시')
    expect((q('wp-step-text') as HTMLTextAreaElement).value).toBe('기존 설계지시')
    expect(text(q('wp-step-file-name'))).toBe('design.txt')
    expect(api.putRequest).not.toHaveBeenCalled()
  })
})

describe('T0017 card-owned instruction and design stages', () => {
  async function change(id: string, value: string, root: ParentNode = document.body) {
    const input = q(id, root) as HTMLInputElement
    input.value = value
    input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
    await flushPromises()
  }

  it.each(['D', 'P', 'L', 'DB'])('%s independently edits AI, review, note and both pencils, saves and reopens', async (type) => {
    const body = planBody() as any
    body.quantities = { ...body.quantities, D: { unit: 'sheet', count: 0 }, [type]: { unit: 'sheet', count: 1 } }
    if (!body.counted_types.includes(type)) body.counted_types.push(type)
    body.steps[0] = { ...body.steps[0], type, key: `${type}#1`, pre_instruction_text: 'legacy shared input',
      pre_instruction_attachment: { original_filename: 'design.txt' } }
    answer = readAnswer({ body, step_execution_status: [] })
    putAnswer = (payload) => { const saved = savedAnswer(payload); answer = { ...answer, ...saved }; return saved }
    await open()
    await click(q('wp-edit-card-toggle', card('c_d1')))
    const instruction = row('c_d1|main')!, design = row('c_d1|result')!
    expect(text(q('wp-edit-step-type', instruction))).toContain('설계 지시')
    expect(text(q('wp-edit-step-type', design))).toContain('설계서')
    for (const current of [instruction, design]) {
      for (const id of ['wp-edit-step-provider', 'wp-edit-step-review', 'wp-edit-step-note', 'wp-edit-step-edit'])
        expect(q(id, current)).not.toBeNull()
    }
    expect(q('wp-edit-step-omit', instruction)).not.toBeNull() // existing approval shortcut retained
    expect(q('wp-edit-step-omit', design)).toBeNull()
    const provider = q('wp-edit-step-provider', instruction) as HTMLSelectElement
    const chosen = [...provider.options].find((option) => option.value && option.value !== provider.value)!.value
    await change('wp-edit-step-provider', chosen, instruction)
    await change('wp-edit-step-note', 'instruction note', instruction)
    await click(q('wp-edit-step-review', instruction))
    await change('wp-edit-review-count', '2')
    await change('wp-edit-review-ai', chosen)
    await click(q('wp-edit-review-done'))
    await click(q('wp-edit-step-edit', instruction))
    expect(text(q('wp-step-heading'))).toContain('설계 지시')
    await change('wp-step-text', 'independent instruction')
    await click(q('wp-step-save'))
    await click(q('wp-edit-step-edit', design))
    expect(text(q('wp-step-heading'))).toContain('설계서')
    expect((q('wp-step-text') as HTMLTextAreaElement).value).toBe('legacy shared input')
    expect(text(q('wp-step-file-name'))).toBe('design.txt')
    await change('wp-step-text', 'independent design editing text')
    await click(q('wp-step-save'))
    await toggle('c_d1', true)
    expect(row('c_d1|main')).toBeNull()
    expect(q('wp-edit-step-edit', row('c_d1|result')!)).not.toBeNull()
    await click(q('wp-edit-save'))
    const storedInstruction = sentStep(puts()[0], `DS${type}#1`)
    const storedDesign = sentStep(puts()[0], `${type}#1`)
    expect(storedInstruction).toMatchObject({ pair_role: 'instruction', provider_id: chosen,
      review_count: 2, reviewer_provider_id: chosen, note: 'instruction note',
      pre_instruction_text: 'independent instruction', instruction_mode: 'omit', card_id: 'c_d1' })
    expect(storedDesign).toMatchObject({ ...body.steps[0], pair_role: 'result', pair_key: `DS${type}#1`,
      pre_instruction_text: 'independent design editing text', origin: 'human' })
    expect(storedDesign.instruction_mode).toBeUndefined()
    closeAllWorkPlanEditWindows(); await flushPromises(); await open()
    expect(mode('c_d1').checked).toBe(true)
    await toggle('c_d1', false)
    await click(q('wp-edit-card-toggle', card('c_d1')))
    expect((q('wp-edit-step-provider', row('c_d1|main')!) as HTMLSelectElement).value).toBe(chosen)
    expect(text(q('wp-edit-step-review', row('c_d1|main')!))).toContain('2')
    await click(q('wp-edit-step-edit', row('c_d1|result')!))
    expect((q('wp-step-text') as HTMLTextAreaElement).value).toBe('independent design editing text')
  })

  it('started design cards keep both stages and refuse omission edits', async () => {
    answer = readAnswer({ step_execution_status: [{ card_id: 'c_d1', step_key: 'D#1', started: true }] })
    await open()
    expect(mode('c_d1').disabled).toBe(true)
    await toggle('c_d1', true)
    expect(row('c_d1|main')).not.toBeNull()
    expect(row('c_d1|result')).not.toBeNull()
  })
})
