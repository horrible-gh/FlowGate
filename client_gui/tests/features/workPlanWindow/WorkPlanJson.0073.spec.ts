/**
 * flowgate.v02.0073 T#4 — JSON in and out of the work plan edit window, on the client only (no server API):
 * - the model (workPlanJson): the export is the body the save sends (bodyOf: wp_version as it is, DS / T2 Steps,
 *   quantities, counted_types, x_* fields, review and pre-instruction); reading it back gives the same body; a JSON
 *   the client can tell is wrong is refused with why (not JSON, not an object, a field missing or of the wrong shape,
 *   another version, quantities ⇔ counted_types, a quantity, a Step's shape, a Step value the window reads of the
 *   wrong shape, an instruction without its result, a card id of the wrong shape / not shared by a set / shared by
 *   two cards, quantities ⇔ cards, the started cards changed) and the draft stays as it was;
 * - the window (mounted by DocumentWindows, `@shared/api` mocked): the title bar's [JSON 가져오기] [JSON 내보내기]
 *   [JSON 원문 보기]; the raw view is a read-only JSON window that follows the draft; an import (paste or file) puts
 *   the plan in the draft — dirty, nothing sent — and [저장] PUTs that body whole with base_revision_no; a refused
 *   JSON says why in names (no type code) and changes nothing; locked, or with a Step window holding edits, there is
 *   no import (the export and the raw view stay); a file still being read when the import window is cancelled,
 *   closed or turned into the raw view is dropped (rejection rej_01M59PPF5FTF5KK4).
 * - flowgate.v02.0074 T#2: the document [업로드] of the plan opens this window's import (a new window once the plan is
 *   read, an open one reused; blocked or unread: no import window; saved by [저장] only), and the document [다운로드]
 *   file — the stored plan, not the draft — read back into the same revision is no change.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DocumentWindows, closeAllDocumentAiWindows } from '@/features/documentWindow'
import { documentIoDownload, documentIoNotices, resetDocumentIo } from '@/features/documentWindow/documentIo'
import {
  closeAllWorkPlanEditWindows,
  openWorkPlanEditImport,
  openWorkPlanEditWindow,
  setWorkPlanEditOpener,
  setWorkPlanStepEditHandler,
  workPlanEditWindowList,
  workPlanEditWindowOf,
} from '@/features/workPlanWindow'
import { bodyOf, draftOf, editBodyOf, rowOf, setStepNote, stepHasInstruction, type WorkPlanDraft } from '@/features/workPlanWindow/workPlanEditModel'
import { readWorkPlanJson, workPlanJsonFileName, workPlanJsonText } from '@/features/workPlanWindow/workPlanJson'
import { DOC, hiddenPlanBody, hiddenSteps, planBody, readAnswer } from './workPlanFixtures'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), putRequest: vi.fn(), postRequest: vi.fn() }))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, ...api }
})

registerAppMessages(i18n)

// --- the model -------------------------------------------------------------------------------------------------------

const draftFrom = (body: Record<string, unknown> = hiddenPlanBody()): WorkPlanDraft => draftOf(editBodyOf(body))
const NONE: ReadonlySet<string> = new Set()
/** The fixture's started card (readAnswer step_execution_status). */
const STARTED: ReadonlySet<string> = new Set(['c_d1'])

/** hiddenPlanBody() as JSON, changed by `change`. Steps: DS#1 D#1 T#1 TR#1 T2#1 TR2#1 T#2 TR#2 TS#1 TSR#1. */
function json(change?: (body: Record<string, any>) => void): string {
  const body = hiddenPlanBody() as Record<string, any>
  change?.(body)
  return JSON.stringify(body)
}

function codes(text: string, current: WorkPlanDraft = draftFrom(), started: ReadonlySet<string> = NONE): string[] {
  const read = readWorkPlanJson(text, current, started)
  return read.ok ? [] : read.issues.map((issue) => issue.code)
}

describe('the plan as JSON (model, 0073 T#4)', () => {
  it('exports the body the save sends — wp_version, DS / T2, quantities, counted_types, x_* — and reads it back the same', () => {
    const draft = draftFrom()
    const text = workPlanJsonText(draft)
    expect(text.endsWith('}\n')).toBe(true)
    const out = JSON.parse(text)
    expect(out).toEqual(bodyOf(draft))
    expect(out).toEqual(hiddenPlanBody())
    expect(out.wp_version).toBe(2)
    expect(out.counted_types).toEqual(['DS', 'D', 'P', 'L', 'DB', 'N', 'T', 'T2', 'TS'])
    expect(out.quantities.DS).toEqual({ unit: 'sheet', count: 1 })
    expect(out.quantities.T2).toEqual({ unit: 'set', count: 1 })
    expect(hiddenSteps(out)).toEqual(hiddenSteps(hiddenPlanBody()))
    expect(out.x_origin_note).toEqual({ kept: true })
    expect(out.steps[0].x_hidden).toEqual({ kept: true })

    const read = readWorkPlanJson(text, draft, STARTED)
    expect(read.ok).toBe(true)
    if (!read.ok) return
    expect(bodyOf(draftOf(read.body))).toEqual(out)
    const values = (steps: Array<Record<string, unknown>>) =>
      steps.map((s) => [s.key, s.review_count, s.reviewer_provider_id, s.pre_instruction_text, s.pre_instruction_attachment])
    expect(values(read.body.steps)).toEqual(values(hiddenPlanBody().steps as Array<Record<string, unknown>>))
  })

  it('the export follows the draft (unsaved edits included); a plan read into another keeps its hidden Steps', () => {
    const edited = setStepNote(draftFrom(), 'c_t2|main', '바꾼 메모')
    expect(JSON.parse(workPlanJsonText(edited)).steps[6].note).toBe('바꾼 메모')

    const read = readWorkPlanJson(workPlanJsonText(draftFrom()), draftFrom(planBody()), NONE)
    expect(read.ok).toBe(true)
    if (read.ok) expect(hiddenSteps(bodyOf(draftOf(read.body)))).toEqual(hiddenSteps(hiddenPlanBody()))
  })

  it.each([
    ['syntax', '{ "wp_version": 2,'],
    ['not_object', '[]'],
    ['not_object', '"plan"'],
    ['missing_field', json((b) => delete b.steps)],
    ['missing_field', json((b) => delete b.defaults)],
    ['field_type', json((b) => (b.quantities = []))],
    ['field_type', json((b) => (b.wp_version = '2'))],
    ['wp_version', json((b) => (b.wp_version = 3))],
    ['unknown_type', json((b) => { b.counted_types.push('X'); b.quantities.X = { unit: 'sheet', count: 0 } })],
    ['quantities_types', json((b) => delete b.quantities.P)],
    ['quantities_types', json((b) => b.counted_types.push('D'))],
    ['quantity', json((b) => (b.quantities.T.count = 21))],
    ['quantity', json((b) => (b.quantities.T.count = 1.5))],
    ['quantity', json((b) => (b.quantities.D.unit = 'set'))],
    ['step', json((b) => delete b.steps[1].key)],
    ['step', json((b) => (b.steps[2].pair_role = 'lead'))],
    ['step', json((b) => (b.steps[9].locked = false))],
    ['step', json((b) => (b.steps[7].key = 'T#2'))],
    ['field_type', json((b) => (b.provider_candidates = [null]))],
    // values the window reads as it draws a row (stepHasInstruction trims the pre-instruction)
    ['step_value', json((b) => (b.steps[2].pre_instruction_text = 123))],
    ['step_value', json((b) => (b.steps[2].pre_instruction_text = { text: '사전 지시' }))],
    ['step_value', json((b) => (b.steps[2].pre_instruction_attachment = 'brief.txt'))],
    ['step_value', json((b) => (b.steps[6].note = 5))],
    ['step_value', json((b) => (b.steps[2].review_count = '2'))],
    ['step_value', json((b) => (b.steps[1].provider_id = { id: 'aip_opus' }))],
    ['step_value', json((b) => (b.steps[1].locked = 'no'))],
    // card ids: the server's format, never a name the window gives a card itself, the same on a set's two Steps
    ['card_id', json((b) => (b.steps[1].card_id = 'retired:c_d1'))],
    ['card_id', json((b) => (b.steps[1].card_id = 'legacy:T#1'))],
    ['card_id', json((b) => (b.steps[1].card_id = 'tmp:1'))],
    ['card_id', json((b) => (b.steps[1].card_id = ''))],
    ['card_id', json((b) => (b.steps[1].card_id = 7))],
    ['card_id', json((b) => (b.steps[3].card_id = 'c_t9'))],
    ['card_id_duplicate', json((b) => { b.steps[6].card_id = 'c_t1'; b.steps[7].card_id = 'c_t1' })],
    ['pair', json((b) => b.steps.splice(3, 1))],
    ['pair', json((b) => (b.steps[3].pair_key = 'T#9'))],
    ['pair', json((b) => (b.steps[1].pair_role = 'instruction'))],
    ['step_count', json((b) => (b.quantities.T.count = 3))],
  ])('refuses %s', (code, text) => {
    expect(codes(text)).toContain(code)
  })

  it('a refused JSON names a type and a Step by place, never by a Step key', () => {
    const read = readWorkPlanJson(json((b) => b.steps.splice(3, 1)), draftFrom(), NONE)
    expect(read.ok ? null : read.issues).toEqual([{ code: 'pair', n: 3 }])
    const count = readWorkPlanJson(json((b) => (b.quantities.T.count = 3)), draftFrom(), NONE)
    expect(count.ok ? null : count.issues).toEqual([{ code: 'step_count', type: 'T' }])
  })

  it('a pre-instruction that is a number or an object is refused before the draft (the row could not be drawn)', () => {
    const current = draftFrom()
    const before = JSON.stringify(bodyOf(current))
    for (const value of [123, { text: '사전 지시' }]) {
      const read = readWorkPlanJson(json((b) => (b.steps[2].pre_instruction_text = value)), current, NONE)
      expect(read.ok ? null : read.issues).toEqual([{ code: 'step_value', n: 3 }])
    }
    expect(JSON.stringify(bodyOf(current))).toBe(before)
    // what is taken can be drawn: every row's pre-instruction mark reads without throwing
    const taken = readWorkPlanJson(json((b) => (b.steps[2].pre_instruction_text = null)), current, NONE)
    expect(taken.ok).toBe(true)
    if (taken.ok) expect(() => draftOf(taken.body).cards.flatMap((card) => card.steps).map(stepHasInstruction)).not.toThrow()
  })

  it('two cards sharing a card id are refused (a row would edit another card); a body without card ids is still taken', () => {
    const current = draftFrom()
    const before = JSON.stringify(bodyOf(current))
    const shared = readWorkPlanJson(json((b) => { b.steps[6].card_id = 'c_t1'; b.steps[7].card_id = 'c_t1' }), current, NONE)
    expect(shared.ok ? null : shared.issues).toEqual([{ code: 'card_id_duplicate', n: 7 }])
    const pair = readWorkPlanJson(json((b) => (b.steps[3].card_id = 'c_t2')), current, NONE)
    expect(pair.ok ? null : pair.issues).toEqual([{ code: 'card_id', n: 4 }])
    expect(JSON.stringify(bodyOf(current))).toBe(before)

    // no card ids (a legacy body) or a new card without one: each card keeps a row name of its own
    for (const change of [
      (b: Record<string, any>) => { for (const s of b.steps) delete s.card_id },
      (b: Record<string, any>) => { b.steps[6].card_id = null; b.steps[7].card_id = null },
    ]) {
      const read = readWorkPlanJson(json(change), current, NONE)
      expect(read.ok).toBe(true)
      if (!read.ok) continue
      const draft = draftOf(read.body)
      const ids = draft.cards.map((card) => card.id)
      expect(new Set(ids).size).toBe(ids.length)
      const edited = setStepNote(draft, `${draft.cards[4].id}|main`, '두 번째 작업')
      expect(rowOf(edited, `${draft.cards[2].id}|main`)?.step.note).toBeNull()
      expect(rowOf(edited, `${draft.cards[4].id}|main`)?.step.note).toBe('두 번째 작업')
    }
  })

  it('the started cards: up to the last one they stay as they are, in their order; cards after them may move', () => {
    // T#1 put before the started D#1
    const moved = json((b) => {
      const [ds, d1, ...rest] = b.steps
      b.steps = [ds, rest[0], rest[1], d1, ...rest.slice(2)]
    })
    expect(codes(moved, draftFrom(), STARTED)).toEqual(['started'])
    // the started D#1 removed
    const removed = json((b) => {
      b.quantities.D.count = 0
      b.steps.splice(1, 1)
    })
    expect(codes(removed, draftFrom(), STARTED)).toEqual(['started'])
    // the started card's id on another type
    const retyped = json((b) => {
      b.quantities.D.count = 0
      b.quantities.P.count = 1
      Object.assign(b.steps[1], { key: 'P#1', type: 'P' })
    })
    expect(codes(retyped, draftFrom(), STARTED)).toEqual(['started'])
    // nothing started: the same moves are the server's to check
    expect(codes(moved, draftFrom(), NONE)).toEqual([])
    // the test set moved before T#1 — after the started cards — is taken
    const after = json((b) => {
      const ts = b.steps.splice(8, 2)
      b.steps.splice(2, 0, ...ts)
    })
    expect(codes(after, draftFrom(), STARTED)).toEqual([])
  })

  it('the export file is named after the document', () => {
    expect(workPlanJsonFileName(DOC)).toBe('flowgate.v02.0072.0006-WP.json')
    expect(workPlanJsonFileName('a/b c')).toBe('a_b_c.json')
    expect(workPlanJsonFileName('')).toBe('work-plan.json')
  })
})

// --- the window ------------------------------------------------------------------------------------------------------

const WP_PATH = `/api/v1/documents/${encodeURIComponent(DOC)}/work-plan`

let answer: Record<string, unknown>
let wrapper: VueWrapper<any> | null = null

function savedAnswer(payload: { base_revision_no: number; body: Record<string, any> }): Record<string, unknown> {
  const body = JSON.parse(JSON.stringify(payload.body))
  for (const s of body.steps) if (!s.card_id) s.card_id = `c_new_${s.pair_role === 'result' ? s.pair_key : s.key}`
  return { ok: true, doc_id: DOC, revision_no: payload.base_revision_no + 1, title: '작업계획 (저장됨)', doc_review_status: 'pending_review', body, warnings: [] }
}

const q = (id: string, root: ParentNode = document.body) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
const qa = (id: string, root: ParentNode = document.body) => [...root.querySelectorAll<HTMLElement>(`[data-test="${id}"]`)]
const text = (node: Element | null | undefined) => node?.textContent?.replace(/\s+/g, ' ').trim() ?? null
const row = (rowId: string) => document.body.querySelector<HTMLElement>(`[data-test="wp-edit-step"][data-row="${rowId}"]`)
const lastPut = () => api.putRequest.mock.calls.at(-1)?.[1] as { base_revision_no: number; body: Record<string, any> }

async function open(extra: Partial<Parameters<typeof openWorkPlanEditWindow>[0]> = {}) {
  openWorkPlanEditWindow({ docId: DOC, projectId: 'flowgate', title: '작업계획', ...extra })
  await flushPromises()
}

async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}

async function type(input: HTMLElement | null, value: string) {
  if (!(input instanceof HTMLInputElement)) throw new Error('no input')
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}

async function typeArea(area: HTMLElement | null, value: string) {
  if (!(area instanceof HTMLTextAreaElement)) throw new Error('no textarea')
  area.value = value
  area.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}

/** Waits for what a FileReader settles (a few macrotasks). */
async function until(check: () => boolean) {
  for (let i = 0; i < 50 && !check(); i++) {
    await new Promise((resolve) => setTimeout(resolve, 10))
    await flushPromises()
  }
  expect(check()).toBe(true)
}

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })
}

/** A FileReader whose read ends when the test says so (documentIoReadFile reads through the global FileReader). */
class HeldReader {
  static held: HeldReader[] = []
  result: string | null = null
  error: unknown = null
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  readAsText(): void {
    HeldReader.held.push(this)
  }
  finish(content: string): void {
    this.result = content
    this.onload?.()
  }
}

/** Picks a file on the import window's hidden file input. */
function pickFile(content: string): void {
  const input = q('wp-json-file') as HTMLInputElement
  Object.defineProperty(input, 'files', { configurable: true, value: [new File([content], 'plan.json', { type: 'application/json' })] })
  input.dispatchEvent(new Event('change'))
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllDocumentAiWindows()
  closeAllWorkPlanEditWindows()
  setWorkPlanStepEditHandler(null)
  api.getRequest.mockReset()
  api.putRequest.mockReset()
  api.postRequest.mockReset()
  answer = readAnswer({ body: hiddenPlanBody() })
  api.getRequest.mockImplementation(async (path: string) => {
    if (path === WP_PATH) return { data: JSON.parse(JSON.stringify(answer)) }
    if (path === '/api/v1/ai-invoke/active') return { data: { active: false } }
    throw new Error(`unexpected GET ${path}`)
  })
  api.putRequest.mockImplementation(async (_path: string, payload: any) => ({ data: savedAnswer(payload) }))
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

describe('the JSON buttons of the plan window (0073 T#4)', () => {
  it('the title bar has [JSON 가져오기] [JSON 내보내기] [JSON 원문 보기] — not the document upload / download', async () => {
    await open()
    const buttons = ['wp-edit-json-import', 'wp-edit-json-export', 'wp-edit-json-raw'].map((id) => q(id) as HTMLButtonElement)
    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual(['JSON 가져오기', 'JSON 내보내기', 'JSON 원문 보기'])
    expect(buttons.every((b) => !b.disabled)).toBe(true)
    expect(q('wp-edit-json')?.closest('.ui-modeless-titlebar')).not.toBeNull()
    expect(q('document-io')).toBeNull()
  })

  it('[JSON 원문 보기]: a JSON window of its own, read only, with the body the save sends; it follows an edit', async () => {
    await open()
    await click(q('wp-edit-json-raw'))
    expect(document.body.querySelectorAll('.ui-modeless-panel')).toHaveLength(2)
    expect(q('wp-json-window')?.dataset.mode).toBe('raw')
    expect(text(q('wp-json-heading'))).toContain('작업계획 JSON')
    const raw = q('wp-json-raw') as HTMLTextAreaElement
    expect(raw.readOnly).toBe(true)
    expect(JSON.parse(raw.value)).toEqual(hiddenPlanBody())

    await type(q('wp-edit-step-note', row('c_t2|main')!), '원문 확인')
    expect(JSON.parse((q('wp-json-raw') as HTMLTextAreaElement).value).steps[6].note).toBe('원문 확인')

    await click(q('wp-json-close'))
    expect(q('wp-json-window')).toBeNull()
    expect(q('wp-edit-window')).not.toBeNull()
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('[JSON 가져오기] by paste: checked, then the draft (dirty, nothing sent); [저장] PUTs that body whole', async () => {
    await open()
    await click(q('wp-edit-json-import'))
    expect(q('wp-json-window')?.dataset.mode).toBe('import')
    expect((q('wp-json-apply') as HTMLButtonElement).disabled).toBe(true)

    const incoming = hiddenPlanBody() as Record<string, any>
    incoming.steps[6].note = '가져온 메모'
    incoming.steps[0].note = '가져온 설계 지시' // a hidden DS Step
    incoming.steps[4].review_count = 3 // a hidden T2 Step
    incoming.x_imported = { from: 'paste' }
    await typeArea(q('wp-json-input'), JSON.stringify(incoming))
    await click(q('wp-json-apply'))

    expect(q('wp-json-window')).toBeNull()
    expect(q('wp-edit-dirty')).not.toBeNull()
    expect(q('wp-edit-notice')?.dataset.key).toBe('imported')
    expect((q('wp-edit-step-note', row('c_t2|main')!) as HTMLInputElement).value).toBe('가져온 메모')
    expect(qa('wp-edit-card').map((card) => card.dataset.card)).toEqual(['c_d1', 'c_t1', 'c_t2', 'c_ts1'])
    expect(api.putRequest).not.toHaveBeenCalled()

    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
    expect(lastPut().base_revision_no).toBe(2)
    expect(lastPut().body).toEqual(incoming)
    expect(q('wp-edit-dirty')).toBeNull()
  })

  it('[파일 선택]: the file is read and taken the same way', async () => {
    await open()
    await click(q('wp-edit-json-import'))
    const incoming = hiddenPlanBody() as Record<string, any>
    incoming.steps[1].note = '파일에서'
    const input = q('wp-json-file') as HTMLInputElement
    Object.defineProperty(input, 'files', { configurable: true, value: [new File([JSON.stringify(incoming)], 'plan.json', { type: 'application/json' })] })
    input.dispatchEvent(new Event('change'))
    await until(() => q('wp-json-window') === null)

    expect(q('wp-edit-dirty')).not.toBeNull()
    await click(q('wp-edit-save'))
    expect(lastPut().body).toEqual(incoming)
  })

  it('a JSON the client refuses: why, in names (no type code or Step key); the draft and the server untouched', async () => {
    await open()
    await click(q('wp-edit-json-import'))

    await typeArea(q('wp-json-input'), '{ "wp_version": 2,')
    await click(q('wp-json-apply'))
    expect(qa('wp-json-issue').map((issue) => issue.dataset.code)).toEqual(['syntax'])
    expect(text(q('wp-json-issue'))).toBe('JSON 형식이 아닙니다.')
    expect((q('wp-json-input') as HTMLTextAreaElement).getAttribute('aria-invalid')).toBe('true')
    expect(q('wp-json-window')).not.toBeNull()

    // typing again clears the reasons
    await typeArea(q('wp-json-input'), json((b) => (b.quantities.T.count = 3)))
    expect(qa('wp-json-issue')).toHaveLength(0)
    await click(q('wp-json-apply'))
    expect(qa('wp-json-issue').map((issue) => issue.dataset.code)).toEqual(['step_count'])
    expect(text(q('wp-json-issue'))).toBe('작업 수량과 단계 수가 다릅니다.')

    await typeArea(q('wp-json-input'), json((b) => b.steps.splice(3, 1)))
    await click(q('wp-json-apply'))
    expect(text(q('wp-json-issue'))).toBe('3번째 단계의 지시서와 결과 짝이 맞지 않습니다.')

    await typeArea(q('wp-json-input'), json((b) => (b.wp_version = 3)))
    await click(q('wp-json-apply'))
    expect(text(q('wp-json-issue'))).toBe('작업계획 형식 버전(3)이 이 작업계획의 버전(2)과 다릅니다.')

    // the started 기본 설계 card (c_d1) put after 작업 1
    await typeArea(q('wp-json-input'), json((b) => {
      const [ds, d1, ...rest] = b.steps
      b.steps = [ds, rest[0], rest[1], d1, ...rest.slice(2)]
    }))
    await click(q('wp-json-apply'))
    expect(qa('wp-json-issue').map((issue) => issue.dataset.code)).toEqual(['started'])

    const shown = qa('wp-json-issue').map((issue) => issue.textContent ?? '').join(' ')
    expect(shown).not.toMatch(/\b(DS|T2|TR|T)#?\d*\b/)
    expect(q('wp-edit-dirty')).toBeNull()
    expect(q('wp-edit-notice')).toBeNull()
    expect(qa('wp-edit-card').map((card) => card.dataset.card)).toEqual(['c_d1', 'c_t1', 'c_t2', 'c_ts1'])
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('[JSON 내보내기]: a .json file of the draft as the save sends it (unsaved edits included); nothing is sent', async () => {
    const files: Array<{ name: string; blob: Blob }> = []
    let last: Blob | null = null
    const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL }
    URL.createObjectURL = vi.fn((blob: Blob) => {
      last = blob
      return 'blob:wp'
    }) as typeof URL.createObjectURL
    URL.revokeObjectURL = vi.fn()
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      files.push({ name: this.download, blob: last as Blob })
    })
    try {
      await open()
      await type(q('wp-edit-step-note', row('c_t1|main')!), '내보낼 메모')
      await click(q('wp-edit-json-export'))
      expect(files).toHaveLength(1)
      expect(files[0].name).toBe(`${DOC}.json`)
      expect(files[0].blob.type).toBe('application/json;charset=utf-8')
      const exported = await readBlob(files[0].blob)
      const expected = hiddenPlanBody() as Record<string, any>
      expected.steps[2].note = '내보낼 메모'
      expect(JSON.parse(exported)).toEqual(expected)
      expect(api.putRequest).not.toHaveBeenCalled()
      expect(q('wp-edit-dirty')).not.toBeNull()

      // the file read back is the plan being edited: taken as no change
      await click(q('wp-edit-json-import'))
      await typeArea(q('wp-json-input'), exported)
      await click(q('wp-json-apply'))
      expect(q('wp-json-window')).toBeNull()
      expect(q('wp-edit-notice')?.dataset.key).toBe('imported_unchanged')
      await click(q('wp-edit-save'))
      expect(lastPut().body).toEqual(expected)
    } finally {
      anchorClick.mockRestore()
      URL.createObjectURL = original.create
      // documentIoSaveFile revokes on a timer: keep a no-op where jsdom has none.
      URL.revokeObjectURL = original.revoke ?? ((() => {}) as typeof URL.revokeObjectURL)
    }
  })

  it('locked: [JSON 가져오기] is off with its reason; the export and the raw view stay', async () => {
    answer = readAnswer({ body: hiddenPlanBody(), editable: false, edit_locked_reason: 'final_approved' })
    await open()
    const importButton = q('wp-edit-json-import') as HTMLButtonElement
    expect(importButton.disabled).toBe(true)
    expect(importButton.title).toBe('지금은 작업계획을 편집할 수 없어 가져올 수 없습니다.')
    expect((q('wp-edit-json-export') as HTMLButtonElement).disabled).toBe(false)
    expect((q('wp-edit-json-raw') as HTMLButtonElement).disabled).toBe(false)
    await click(importButton)
    expect(q('wp-json-window')).toBeNull()
  })

  it('a lock arriving while the import window is open turns it off; nothing is taken', async () => {
    const external = ref<'read_only' | 'run_locked' | null>(null)
    await open({ external: () => external.value })
    await click(q('wp-edit-json-import'))
    await typeArea(q('wp-json-input'), json((b) => (b.steps[6].note = '잠금 뒤')))

    external.value = 'run_locked'
    await flushPromises()
    expect(q('wp-edit-lock')?.dataset.reason).toBe('run_locked')
    expect(q('wp-json-blocked')?.dataset.reason).toBe('locked')
    expect((q('wp-json-apply') as HTMLButtonElement).disabled).toBe(true)
    expect((q('wp-json-choose') as HTMLButtonElement).disabled).toBe(true)
    expect((q('wp-json-input') as HTMLTextAreaElement).disabled).toBe(true)
    await click(q('wp-json-apply'))
    expect(q('wp-edit-dirty')).toBeNull()
    expect((q('wp-edit-step-note', row('c_t2|main')!) as HTMLInputElement).value).toBe('')
  })

  it('a Step window holding edits: [JSON 가져오기] is off until they are saved or dropped', async () => {
    setWorkPlanStepEditHandler({ open: () => {}, pending: () => true })
    await open()
    const importButton = q('wp-edit-json-import') as HTMLButtonElement
    expect(importButton.disabled).toBe(true)
    expect(importButton.title).toBe('단계 상세 편집 창의 변경을 저장하거나 취소한 뒤 가져오세요.')
  })

  it('a pre-instruction the window cannot draw, or a card id two cards share: refused with why; the draft stays drawn and clean', async () => {
    await open()
    await click(q('wp-edit-json-import'))

    await typeArea(q('wp-json-input'), json((b) => (b.steps[2].pre_instruction_text = 123)))
    await click(q('wp-json-apply'))
    expect(qa('wp-json-issue').map((issue) => issue.dataset.code)).toEqual(['step_value'])
    expect(text(q('wp-json-issue'))).toBe('3번째 단계의 값(AI·메모·검수·사전 지시서) 형식이 맞지 않습니다.')

    await typeArea(q('wp-json-input'), json((b) => (b.steps[2].pre_instruction_text = { text: '사전 지시' })))
    await click(q('wp-json-apply'))
    expect(qa('wp-json-issue').map((issue) => issue.dataset.code)).toEqual(['step_value'])

    // the second work set given the first one's card id
    await typeArea(q('wp-json-input'), json((b) => { b.steps[6].card_id = 'c_t1'; b.steps[7].card_id = 'c_t1' }))
    await click(q('wp-json-apply'))
    expect(qa('wp-json-issue').map((issue) => issue.dataset.code)).toEqual(['card_id_duplicate'])
    expect(text(q('wp-json-issue'))).toBe('7번째 단계의 카드 식별자가 다른 카드와 같습니다.')

    expect(q('wp-json-window')).not.toBeNull()
    expect(q('wp-edit-dirty')).toBeNull()
    expect(q('wp-edit-notice')).toBeNull()
    expect(qa('wp-edit-card').map((card) => card.dataset.card)).toEqual(['c_d1', 'c_t1', 'c_t2', 'c_ts1'])
    expect(q('wp-edit-step-instruction', row('c_t1|main')!)).not.toBeNull()

    // the second work set's row edits that row only
    await click(q('wp-json-cancel'))
    await type(q('wp-edit-step-note', row('c_t2|main')!), '두 번째 작업')
    expect((q('wp-edit-step-note', row('c_t1|main')!) as HTMLInputElement).value).toBe('')
    await click(q('wp-edit-save'))
    const expected = hiddenPlanBody() as Record<string, any>
    expected.steps[6].note = '두 번째 작업'
    expect(lastPut().body).toEqual(expected)
  })

  it('a file still being read when the import window is cancelled, closed or turned into the raw view is dropped', async () => {
    HeldReader.held = []
    vi.stubGlobal('FileReader', HeldReader)
    try {
      await open()
      const incoming = json((b) => (b.steps[6].note = '취소한 파일'))
      const untouched = () => {
        expect(q('wp-edit-dirty')).toBeNull()
        expect(q('wp-edit-notice')).toBeNull()
        expect((q('wp-edit-step-note', row('c_t2|main')!) as HTMLInputElement).value).toBe('')
        expect(api.putRequest).not.toHaveBeenCalled()
      }

      // [취소] while reading, then a new import window: the old read ends and changes nothing, the new window stays
      await click(q('wp-edit-json-import'))
      pickFile(incoming)
      await flushPromises()
      expect(HeldReader.held).toHaveLength(1)
      expect((q('wp-json-choose') as HTMLButtonElement).disabled).toBe(true)
      await click(q('wp-json-cancel'))
      expect(q('wp-json-window')).toBeNull()
      await click(q('wp-edit-json-import'))
      HeldReader.held[0].finish(incoming)
      await flushPromises()
      expect(q('wp-json-window')?.dataset.mode).toBe('import')
      expect((q('wp-json-input') as HTMLTextAreaElement).value).toBe('')
      expect(qa('wp-json-issue')).toHaveLength(0)
      untouched()

      // × of the JSON window while reading
      pickFile(incoming)
      await flushPromises()
      const panel = q('wp-json-window')?.closest('.ui-modeless-panel') as HTMLElement
      await click(panel.querySelector<HTMLElement>('[aria-label="Close"]'))
      expect(q('wp-json-window')).toBeNull()
      HeldReader.held[1].finish(incoming)
      await flushPromises()
      expect(q('wp-json-window')).toBeNull()
      untouched()

      // [JSON 원문 보기] while reading: the raw view takes the import window's place and stays
      await click(q('wp-edit-json-import'))
      pickFile(incoming)
      await flushPromises()
      await click(q('wp-edit-json-raw'))
      expect(q('wp-json-window')?.dataset.mode).toBe('raw')
      HeldReader.held[2].finish(incoming)
      await flushPromises()
      expect(q('wp-json-window')?.dataset.mode).toBe('raw')
      expect(JSON.parse((q('wp-json-raw') as HTMLTextAreaElement).value)).toEqual(hiddenPlanBody())
      untouched()

      // a read that ends with its window open is taken as before
      await click(q('wp-json-close'))
      await click(q('wp-edit-json-import'))
      pickFile(incoming)
      await flushPromises()
      HeldReader.held[3].finish(incoming)
      await flushPromises()
      expect(q('wp-json-window')).toBeNull()
      expect(q('wp-edit-dirty')).not.toBeNull()
      expect((q('wp-edit-step-note', row('c_t2|main')!) as HTMLInputElement).value).toBe('취소한 파일')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

// --- flowgate.v02.0074 T#2: the document [업로드] / [다운로드] of the plan ------------------------------------------------

describe('the document [업로드] opens this window’s [JSON 가져오기]; the [다운로드] file reads back unchanged (0074 T#2)', () => {
  const opener = vi.fn((_docId: string, _flowId: string) => ({ external: () => null, saved: () => {} }))

  beforeEach(() => {
    resetDocumentIo()
    opener.mockClear()
    setWorkPlanEditOpener(opener)
  })

  afterEach(() => {
    setWorkPlanEditOpener(null)
    resetDocumentIo()
  })

  /** The plan's .json as the document [다운로드] saves it (the server's stored body). */
  async function downloaded(): Promise<string> {
    const files: Blob[] = []
    await documentIoDownload(DOC, { save: (blob) => void files.push(blob) })
    expect(files).toHaveLength(1)
    return readBlob(files[0])
  }

  it('a new window opens the import window once the plan is read — once per request; nothing is sent', async () => {
    openWorkPlanEditImport(DOC, { projectId: 'flowgate', groupId: null })
    expect(opener).toHaveBeenCalledWith(DOC, 'flowgate.v02.0072')
    await flushPromises()
    expect(workPlanEditWindowList()).toHaveLength(1)
    expect(q('wp-edit-window')).not.toBeNull()
    expect(q('wp-json-window')?.dataset.mode).toBe('import')

    await click(q('wp-json-cancel'))
    expect(q('wp-json-window')).toBeNull()
    await type(q('wp-edit-step-note', row('c_t2|main')!), '편집')
    expect(q('wp-json-window')).toBeNull()
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('an open window is reused (one window, its external · saved kept, raised); over the raw view the import opens', async () => {
    const external = () => null
    const saved = vi.fn()
    await open({ external, saved })
    await click(q('wp-edit-json-raw'))
    expect(q('wp-json-window')?.dataset.mode).toBe('raw')

    openWorkPlanEditImport(DOC, { projectId: 'flowgate', groupId: null })
    await flushPromises()
    expect(workPlanEditWindowList()).toHaveLength(1)
    const win = workPlanEditWindowOf(DOC)!
    expect(win.raise).toBe(1)
    expect(win.request.external).toBe(external)
    expect(win.request.saved).toBe(saved)
    expect(opener).not.toHaveBeenCalled()
    expect(document.body.querySelectorAll('[data-test="wp-edit-window"]')).toHaveLength(1)
    expect(q('wp-json-window')?.dataset.mode).toBe('import')

    // asked again after it was closed: it opens again (a new request)
    await click(q('wp-json-cancel'))
    openWorkPlanEditImport(DOC, { projectId: 'flowgate', groupId: null })
    await flushPromises()
    expect(win.raise).toBe(2)
    expect(q('wp-json-window')?.dataset.mode).toBe('import')
  })

  it('blocked when asked (locked, a Step window holding edits): no import window; the button says why', async () => {
    const external = ref<'read_only' | 'run_locked' | null>('run_locked')
    await open({ external: () => external.value })
    openWorkPlanEditImport(DOC, { projectId: 'flowgate', groupId: null })
    await flushPromises()
    expect(q('wp-json-window')).toBeNull()
    const importButton = q('wp-edit-json-import') as HTMLButtonElement
    expect(importButton.disabled).toBe(true)
    expect(importButton.title).toBe('지금은 작업계획을 편집할 수 없어 가져올 수 없습니다.')
    // the request was handled: the lock ending opens nothing by itself
    external.value = null
    await flushPromises()
    expect(q('wp-json-window')).toBeNull()

    setWorkPlanStepEditHandler({ open: () => {}, pending: () => true })
    openWorkPlanEditImport(DOC, { projectId: 'flowgate', groupId: null })
    await flushPromises()
    expect(q('wp-json-window')).toBeNull()
    expect((q('wp-edit-json-import') as HTMLButtonElement).title).toBe('단계 상세 편집 창의 변경을 저장하거나 취소한 뒤 가져오세요.')
  })

  it('a plan that could not be read opens no import window', async () => {
    api.getRequest.mockImplementation(async (path: string) => {
      if (path === WP_PATH) throw { isAxiosError: true, response: { status: 404, data: { detail: 'x' } } }
      if (path === '/api/v1/ai-invoke/active') return { data: { active: false } }
      throw new Error(`unexpected GET ${path}`)
    })
    openWorkPlanEditImport(DOC, { projectId: 'flowgate', groupId: null })
    await flushPromises()
    expect(q('wp-edit-window')).not.toBeNull()
    expect(q('wp-json-window')).toBeNull()
  })

  it('the import only replaces the draft; [저장] is the one PUT with base_revision_no', async () => {
    openWorkPlanEditImport(DOC, { projectId: 'flowgate', groupId: null })
    await flushPromises()
    await typeArea(q('wp-json-input'), json((b) => (b.steps[6].note = '업로드로 가져옴')))
    await click(q('wp-json-apply'))
    expect(q('wp-edit-dirty')).not.toBeNull()
    expect(workPlanEditWindowOf(DOC)?.dirty).toBe(true)
    expect(api.putRequest).not.toHaveBeenCalled()
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
    expect(lastPut().base_revision_no).toBe(2)
    expect(lastPut().body.steps[6].note).toBe('업로드로 가져옴')
    expect(workPlanEditWindowOf(DOC)?.dirty).toBe(false)
  })

  it('round trip: the downloaded .json put back into the same revision’s window is no change (not dirty)', async () => {
    const text = await downloaded()
    expect(text).toBe(`${JSON.stringify(answer.body, null, 2)}\n`)
    openWorkPlanEditImport(DOC, { projectId: 'flowgate', groupId: null })
    await flushPromises()
    await typeArea(q('wp-json-input'), text)
    await click(q('wp-json-apply'))
    expect(q('wp-json-window')).toBeNull()
    expect(q('wp-edit-notice')?.dataset.key).toBe('imported_unchanged')
    expect(q('wp-edit-dirty')).toBeNull()
    expect(workPlanEditWindowOf(DOC)?.dirty).toBe(false)
    expect((q('wp-edit-save') as HTMLButtonElement).disabled).toBe(true)
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('the download is the stored plan, not the window’s unsaved draft, and says so once', async () => {
    await open()
    await type(q('wp-edit-step-note', row('c_t2|main')!), '저장 안 한 메모')
    expect(workPlanEditWindowOf(DOC)?.dirty).toBe(true)
    const text = await downloaded()
    expect(JSON.parse(text)).toEqual(hiddenPlanBody())
    expect(documentIoNotices().map((item) => item.key)).toEqual(['download_unsaved_excluded'])
  })
})
