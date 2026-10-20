// 0072 T#2: real parent/child ModelessShells, mocked HTTP at the transport boundary.
// Reserved-file state models the existing successful-PUT cleanup contract; no server writes.
// 0073 T#2: the Step window has no review controls and never names the Step key; a review set in the parent's review
// popover is kept through the Step window's save and cancel (its draft carries no review); an Esc in the Step window
// is the Step window's, never the popover's.
// 0073 T#3: a file dropped on the attachment area takes the picker's checks and upload (result step, new card, size,
// lock, failure classification); the area is icon / name / choose-replace-remove, and the long notes are tooltips
// (native titles) or error lines while the checks they describe still hold.
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { closeAllDocumentAiWindows } from '@/features/documentWindow'
import { documentIoConflict } from '@/features/documentWindow/documentEditRegistry'
import { closeAllWorkPlanEditWindows, openWorkPlanEditWindow, WorkPlanEditWindows } from '@/features/workPlanWindow'
import { workPlanUploadInstruction } from '@/features/document'
import { dropWorkPlanStepFiles, saveWorkPlanStep, setWorkPlanStepValue, uploadWorkPlanStepFile, workPlanStepDirty, workPlanStepWindowList, workPlanStepPending } from '@/features/workPlanWindow/workPlanStepEditWindows'
import { DOC, planBody, readAnswer } from './workPlanFixtures'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), putRequest: vi.fn(), postFormRequest: vi.fn() }))
vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), ...api }))
registerAppMessages(i18n)
const WP = '/api/v1/documents/' + encodeURIComponent(DOC) + '/work-plan'
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const refFile = (name = 'new.txt') => ({ doc_id: DOC, filename: '__wp_pre_instruction__T-1__' + name, original_filename: name, content_sha256: 'b'.repeat(64) })
let wrapper: VueWrapper
let answer: Record<string, any>
let activity: Record<string, unknown>
let lock: { value: 'read_only' | 'run_locked' | null }
let reserved: Set<string>
let lastBody: Record<string, any> | null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllWorkPlanEditWindows()
  closeAllDocumentAiWindows()
  api.getRequest.mockReset()
  api.putRequest.mockReset()
  api.postFormRequest.mockReset()
  answer = readAnswer()
  activity = { active: false }
  lock = reactive({ value: null })
  reserved = new Set()
  lastBody = null
  api.getRequest.mockImplementation(async (path: string) => {
    if (path === WP) return { data: copy(answer) }
    if (path === '/api/v1/ai-invoke/active') return { data: activity }
    throw new Error('Unexpected GET ' + path)
  })
  api.postFormRequest.mockImplementation(async (_path: string, form: FormData) => {
    const reference = refFile((form.get('file') as File).name)
    reserved.add(reference.filename)
    return { data: { ok: true, reference } }
  })
  api.putRequest.mockImplementation(async (_path: string, payload: any) => {
    lastBody = copy(payload.body)
    const body = lastBody!
    for (const s of body.steps) if (!s.card_id) s.card_id = 'new_' + (s.pair_role === 'result' ? s.pair_key : s.key)
    const used = new Set(body.steps.map((s: any) => s.pre_instruction_attachment?.filename))
    for (const name of reserved) if (!used.has(name)) reserved.delete(name)
    answer = { ...answer, body, revision_no: payload.base_revision_no + 1 }
    return { data: { ok: true, ...answer } }
  })
  wrapper = mount(WorkPlanEditWindows, { global: { plugins: [i18n] }, attachTo: document.body })
})
afterEach(() => {
  wrapper.unmount()
  closeAllWorkPlanEditWindows()
  closeAllDocumentAiWindows()
  document.body.innerHTML = ''
})

const q = (id: string, root: ParentNode = document.body) => root.querySelector<HTMLElement>('[data-test="' + id + '"]')!
const row = (id: string) => document.body.querySelector<HTMLElement>('[data-test="wp-edit-step"][data-row="' + id + '"]')!
const card = (id: string) => document.body.querySelector<HTMLElement>('[data-test="wp-edit-card"][data-card="' + id + '"]')!
const box = () => document.body.querySelector<HTMLElement>('[data-test="wp-edit-quantity"][data-type="T"]')!
const win = (index = 0) => workPlanStepWindowList()[index]
const child = (index = 0) => document.body.querySelector<HTMLElement>('[data-test="wp-step-window"][data-window="' + win(index).id + '"]')!
const disabled = (node: HTMLElement) => (node as HTMLButtonElement).disabled
const inputValue = (node: HTMLElement) => (node as HTMLInputElement).value
async function click(node: HTMLElement) { expect(node).toBeTruthy(); node.click(); await flushPromises() }
async function type(node: HTMLElement, value: string) {
  (node as HTMLInputElement).value = value
  node.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}
async function pick(node: HTMLElement, value: string) {
  (node as HTMLSelectElement).value = value
  node.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
}
async function confirm(yes = true) {
  const buttons = document.body.querySelectorAll<HTMLButtonElement>('.ui-confirm-actions button')
  await click(buttons[yes ? 1 : 0])
}
async function open(id = 'c_t1|main') {
  openWorkPlanEditWindow({ docId: DOC, projectId: 'flowgate', external: () => lock.value })
  await flushPromises()
  await pencil(id)
}
async function pencil(id: string) { await click(q('wp-edit-step-edit', row(id))) }
async function refresh() {
  window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: DOC } }))
  await flushPromises()
}
async function file(name = 'new.txt', size = 2) {
  const node = q('wp-step-file', child()) as HTMLInputElement
  Object.defineProperty(node, 'files', { configurable: true, value: [new File(['x'.repeat(size)], name)] })
  node.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
}
function reject(status: number, data: Record<string, unknown>) { return { isAxiosError: true, response: { status, data } } }

describe('separate draft and modeless window', () => {
  it('the compact editor keeps unsaved text and attachment edits through review changes', async () => {
    await open()
    expect(q('wp-step-status', child()).textContent).toBe('변경 없음')
    await type(q('wp-step-text', child()), 'compact draft')
    expect(q('wp-step-status', child()).textContent).toBe('미저장')
    await file('compact.txt')
    await click(q('wp-edit-step-review', row('c_t1|main')))
    await pick(q('wp-edit-review-count'), '3')
    await click(q('wp-edit-review-done'))
    await click(q('wp-step-save', child()))
    expect(api.putRequest).not.toHaveBeenCalled()
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1')).toMatchObject({ review_count: 3, pre_instruction_text: 'compact draft', pre_instruction_attachment: { original_filename: 'compact.txt' } })
  })

  it('pencil opens a sibling shell; moving/resizing it leaves the parent in place', async () => {
    await open()
    const parent = q('wp-edit-window').closest<HTMLElement>('.ui-modeless-panel')!
    const panel = child().closest<HTMLElement>('.ui-modeless-panel')!
    expect(parent.contains(child())).toBe(false)
    expect(document.querySelectorAll('.ui-modeless-panel')).toHaveLength(2)
    expect(document.querySelector('.ui-modal-backdrop')).toBeNull()
    const before = parent.style.cssText
    const left = panel.style.left
    panel.querySelector('.ui-modeless-titlebar')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, clientX: 300, clientY: 200 }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 330, clientY: 220 }))
    document.dispatchEvent(new MouseEvent('mouseup'))
    await flushPromises()
    expect(panel.style.left).not.toBe(left)
    const width = panel.style.width
    panel.querySelector('.ui-modeless-resize-handle')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, clientX: 500, clientY: 400 }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 550, clientY: 450 }))
    document.dispatchEvent(new MouseEvent('mouseup'))
    await flushPromises()
    expect(panel.style.width).not.toBe(width)
    expect(parent.style.cssText).toBe(before)
  })

  it('reopening restores and raises the same child with its unsaved draft', async () => {
    await open()
    await type(q('wp-step-text', child()), 'keep this')
    const id = win().id
    const panel = child().closest('.ui-modeless-panel')!
    await click(panel.querySelector<HTMLElement>('[aria-label="Minimize"]')!)
    expect(win().minimized).toBe(true)
    await pencil('c_t1|main')
    expect(workPlanStepWindowList()).toHaveLength(1)
    expect(win().id).toBe(id)
    expect(win().minimized).toBe(false)
    expect(inputValue(q('wp-step-text', child()))).toBe('keep this')
  })

  it('child Save changes only its row; parent and another child keep their own edits until parent PUT', async () => {
    await open()
    await type(q('wp-edit-step-note', row('c_d1|main')), 'parent note')
    await type(q('wp-step-text', child()), 'first child')
    await pencil('c_t2|main')
    await type(q('wp-step-text', child(1)), 'second child')
    expect(disabled(q('wp-edit-save'))).toBe(true)
    await click(q('wp-step-save', child()))
    expect(api.putRequest).not.toHaveBeenCalled()
    expect(workPlanStepWindowList()).toHaveLength(1)
    expect(inputValue(q('wp-step-text', child()))).toBe('second child')
    expect(inputValue(q('wp-edit-step-note', row('c_d1|main')))).toBe('parent note')
    expect(disabled(q('wp-edit-save'))).toBe(true)
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1').pre_instruction_text).toBe('first child')
    expect(lastBody!.steps.find((s: any) => s.key === 'T#2').pre_instruction_text).toBe('second child')
    expect(lastBody!.steps.find((s: any) => s.key === 'DSD#1').note).toBe('parent note')
    expect(lastBody!.x_origin_note).toEqual({ kept: true })
    expect(lastBody!.steps.find((s: any) => s.key === 'TR#1').pre_instruction_text).toBeNull()
  })

  it.each(['cancel', 'close', 'escape'])('%s asks before discarding; reopening uses the parent baseline', async (how) => {
    await open()
    await type(q('wp-step-text', child()), 'discard')
    if (how === 'cancel') await click(q('wp-step-cancel', child()))
    else if (how === 'close') await click(child().closest('.ui-modeless-panel')!.querySelector<HTMLElement>('[aria-label="Close"]')!)
    else {
      child().closest('.ui-modeless-panel')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      await flushPromises()
    }
    await confirm(false)
    expect(win().draft.preInstructionText).toBe('discard')
    await click(q('wp-step-cancel', child()))
    await confirm()
    expect(workPlanStepWindowList()).toHaveLength(0)
    expect(q('wp-edit-dirty')).toBeNull()
    await pencil('c_t1|main')
    expect(inputValue(q('wp-step-text', child()))).toBe('사전 지시')
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('no review controls; the title names the card and document, never the Step key; a result step changes nothing', async () => {
    await open()
    const panel = () => child().closest<HTMLElement>('.ui-modeless-panel')!
    expect(q('wp-step-review', child())).toBeNull()
    expect(q('wp-step-reviewer', child())).toBeNull()
    expect(child().querySelectorAll('select')).toHaveLength(0)
    expect(q('wp-step-title', panel()).textContent).toContain('작업 1 · 지시서')
    expect(q('wp-step-title', panel()).textContent).toContain('단계 편집')
    expect(q('wp-step-heading', child()).textContent).toContain('지시서')
    expect(q('wp-step-heading', child()).textContent).toContain('3번째 단계')
    expect(q('wp-step-heading', child()).textContent).not.toContain('작업 1')
    expect(panel().textContent).not.toMatch(/T#1|TR#1|검수/)
    await click(q('wp-step-cancel', child()))
    await pencil('c_t1|result')
    expect(q('wp-step-heading', child()).textContent).toContain('레포트')
    expect(q('wp-step-heading', child()).textContent).toContain('4번째 단계')
    expect(panel().textContent).not.toMatch(/T#1|TR#1|검수/)
    expect(q('wp-step-text', child())).toBeNull()
    expect(q('wp-step-file', child())).toBeNull()
    expect(q('wp-step-result', child())).toBeTruthy()
    setWorkPlanStepValue(win(), 'preInstructionText', 'forbidden')
    await uploadWorkPlanStepFile(win(), new File(['x'], 'no.txt'))
    expect(win().draft.preInstructionText).toBeNull()
    expect(api.postFormRequest).not.toHaveBeenCalled()
    await click(q('wp-step-save', child()))
    expect(workPlanStepWindowList()).toHaveLength(0)
    expect(q('wp-edit-dirty')).toBeNull()
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('checks Unicode code points at the exact text limit without silently truncating', async () => {
    answer.limits.pre_instruction_text_max_chars = 2
    await open()
    await type(q('wp-step-text', child()), '😀😀')
    expect(q('wp-step-counter', child()).textContent).toContain('2 / 2')
    expect(disabled(q('wp-step-save', child()))).toBe(false)
    await type(q('wp-step-text', child()), '😀😀가')
    expect(disabled(q('wp-step-save', child()))).toBe(true)
    expect(saveWorkPlanStep(win())).toBe(false)
    expect(win().draft.preInstructionText).toBe('😀😀가')
    await type(q('wp-step-text', child()), '😀가')
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1').pre_instruction_text).toBe('😀가')
  })

  it('parent close confirms child-only edits and disposes child sessions', async () => {
    await open()
    const session = win().session
    await type(q('wp-step-text', child()), 'child only')
    await click(q('wp-edit-close'))
    await confirm(false)
    expect(workPlanStepWindowList()).toHaveLength(1)
    await click(q('wp-edit-close'))
    await confirm()
    expect(workPlanStepWindowList()).toHaveLength(0)
    expect(session.alive()).toBe(false)
    expect(api.putRequest).not.toHaveBeenCalled()
  })
})

describe('identity and external state', () => {
  it('reorder follows card id/role and uploads with the stored key, not the displayed ordinal', async () => {
    await open('c_t2|main')
    await type(q('wp-step-text', child()), 'belongs to c_t2')
    await click(q('wp-edit-card-up', card('c_t2')))
    expect(win().session.target()?.stepKey).toBe('T#1')
    await file()
    const form = api.postFormRequest.mock.calls[0][1] as FormData
    expect(form.get('step_key')).toBe('T#2')
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.card_id === 'c_t2' && s.pair_role === 'instruction')).toMatchObject({ key: 'T#1', pre_instruction_text: 'belongs to c_t2', pre_instruction_attachment: refFile() })
    expect(lastBody!.steps.find((s: any) => s.card_id === 'c_t1' && s.pair_role === 'instruction').pre_instruction_text).toBe('사전 지시')
  })

  it('delete and restore cannot rebind an old dirty child to a reused card id', async () => {
    await open('c_t2|main')
    const session = win().session
    await type(q('wp-step-text', child()), 'orphan')
    await click(q('wp-edit-quantity-dec', box()))
    await confirm()
    expect(session.target()).toBeNull()
    expect(q('wp-step-gone', child())).toBeTruthy()
    await click(q('wp-edit-quantity-inc', box()))
    expect(row('c_t2|main')).toBeTruthy()
    expect(session.target()).toBeNull()
    expect(saveWorkPlanStep(win())).toBe(false)
    await click(q('wp-step-cancel', child()))
    await confirm()
    await pencil('c_t2|main')
    expect(win().draft.preInstructionText).toBeNull()
  })

  it('new temporary step follows its server id after parent save and can then attach', async () => {
    await open()
    await click(q('wp-step-cancel', child()))
    await click(q('wp-edit-quantity-inc', box()))
    const newRow = document.querySelector<HTMLElement>('[data-test="wp-edit-step"][data-step="T#3"]')!
    await click(q('wp-edit-step-edit', newRow))
    const session = win().session
    expect(session.target()?.rowId).toContain('tmp:')
    expect(disabled(q('wp-step-choose', child()))).toBe(true)
    await uploadWorkPlanStepFile(win(), new File(['x'], 'new.txt'))
    expect(api.postFormRequest).not.toHaveBeenCalled()
    await click(q('wp-edit-save'))
    expect(session.target()?.rowId).toBe('new_T#3|main')
    expect(disabled(q('wp-step-choose', child()))).toBe(false)
    await file()
    expect((api.postFormRequest.mock.calls[0][1] as FormData).get('step_key')).toBe('T#3')
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'T#3').pre_instruction_attachment).toEqual(refFile())
  })

  it('child-only edits prevent automatic reload and document upload; explicit reload discards them', async () => {
    await open()
    const session = win().session
    await type(q('wp-step-text', child()), 'local child')
    expect(documentIoConflict(DOC)).toBe(true)
    answer = readAnswer({ revision_no: 7, body: { ...planBody(), defaults: { provider_id: null, note: 'external' } } })
    await refresh()
    expect(q('wp-edit-external')).toBeTruthy()
    expect(win().draft.preInstructionText).toBe('local child')
    expect(session.target()).not.toBeNull()
    await click(q('wp-edit-external-reload'))
    await confirm(false)
    expect(workPlanStepWindowList()).toHaveLength(1)
    await click(q('wp-edit-external-reload'))
    await confirm()
    expect(workPlanStepWindowList()).toHaveLength(0)
    expect(session.target()).toBeNull()
    // flowgate.v02.0074 T#1 (R §2.2): no r{n} shown; the read revision 7 is the next parent save's base.
    expect(document.querySelector('[data-test="wp-edit-revision"]')).toBeNull()
    expect(api.putRequest).not.toHaveBeenCalled()
    await type(q('wp-edit-step-note', row('c_d1|main')), 'after reload')
    await click(q('wp-edit-save'))
    expect(api.putRequest.mock.calls.at(-1)![1].base_revision_no).toBe(7)
  })

  it.each(['read_only', 'run_locked'] as const)('a new %s lock keeps the child draft but blocks input/apply/upload', async (reason) => {
    await open()
    await type(q('wp-step-text', child()), 'keep while locked')
    lock.value = reason
    await flushPromises()
    expect(q('wp-step-lock', child()).dataset.reason).toBe(reason)
    expect(disabled(q('wp-step-text', child()))).toBe(true)
    expect(saveWorkPlanStep(win())).toBe(false)
    setWorkPlanStepValue(win(), 'preInstructionText', 'must not apply')
    await uploadWorkPlanStepFile(win(), new File(['x'], 'no.txt'))
    expect(api.postFormRequest).not.toHaveBeenCalled()
    expect(win().draft.preInstructionText).toBe('keep while locked')
    lock.value = null
    await flushPromises()
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1').pre_instruction_text).toBe('keep while locked')
  })

  it.each(['final_approved', 'ai_running'])('refresh detects %s without dropping the child draft', async (reason) => {
    await open()
    await type(q('wp-step-text', child()), 'retain')
    if (reason === 'final_approved') answer = { ...answer, editable: false, edit_locked_reason: reason }
    else activity = { active: true, run_id: null }
    await refresh()
    expect(q('wp-step-lock', child()).dataset.reason).toBe(reason)
    expect(disabled(q('wp-step-save', child()))).toBe(true)
    expect(win().draft.preInstructionText).toBe('retain')
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('a failed parent PUT preserves applied edits and reopens them; retry saves once', async () => {
    await open()
    await type(q('wp-step-text', child()), 'survives failed save')
    await click(q('wp-step-save', child()))
    api.putRequest.mockRejectedValueOnce(reject(409, { code: 'wp_revision_conflict' }))
    await click(q('wp-edit-save'))
    expect(q('wp-edit-conflict')).toBeTruthy()
    await pencil('c_t1|main')
    expect(win().draft.preInstructionText).toBe('survives failed save')
    await click(q('wp-step-cancel', child()))
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(2)
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1').pre_instruction_text).toBe('survives failed save')
  })

  it('conflicting parent details cannot be overwritten by a stale child Save', async () => {
    await open()
    await type(q('wp-step-text', child()), 'child version')
    const parent = win().session.snapshot()!
    expect(win().session.apply({ ...parent, preInstructionText: 'parent version' })).toBe('applied')
    await flushPromises()
    expect(saveWorkPlanStep(win())).toBe(false)
    expect(win().issue).toBe('conflict')
    expect(win().draft.preInstructionText).toBe('child version')
    expect(win().session.snapshot()?.preInstructionText).toBe('parent version')
  })
})

describe('reserved attachment lifecycle', () => {
  it('multipart upload is immediate; replacement/removal remain local until parent PUT cleanup', async () => {
    await open()
    await file('first.txt')
    await file('second.txt')
    expect(api.postFormRequest).toHaveBeenCalledTimes(2)
    expect(api.postFormRequest.mock.calls[0][0]).toBe(WP + '/pre-instruction-attachments')
    expect([...(api.postFormRequest.mock.calls[0][1] as FormData).keys()]).toEqual(['file', 'step_key'])
    expect(q('wp-step-file-name', child()).textContent).toBe('second.txt')
    expect(reserved.size).toBe(2)
    expect(api.putRequest).not.toHaveBeenCalled()
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(reserved).toEqual(new Set([refFile('second.txt').filename]))
    await pencil('c_t1|main')
    await click(q('wp-step-remove', child()))
    expect(reserved.size).toBe(1)
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(reserved.size).toBe(0)
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1').pre_instruction_attachment).toBeNull()
  })

  it('cancel leaves the original reference intact, makes no implicit PUT, and cleanup waits for later successful PUT', async () => {
    await open()
    const original = copy(win().draft.preInstructionAttachment)
    await file()
    await click(q('wp-step-cancel', child()))
    await confirm()
    expect(reserved.size).toBe(1)
    expect(api.putRequest).not.toHaveBeenCalled()
    await pencil('c_t1|main')
    expect(win().draft.preInstructionAttachment).toEqual(original)
    await click(q('wp-step-cancel', child()))
    await type(q('wp-edit-default-note'), 'later explicit save')
    api.putRequest.mockRejectedValueOnce(reject(500, { message: 'server error' }))
    await click(q('wp-edit-save'))
    expect(reserved.size).toBe(1)
    await click(q('wp-edit-save'))
    expect(reserved.size).toBe(0)
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1').pre_instruction_attachment).toEqual(original)
  })

  it.each([403, 413, 422, 500])('upload HTTP %s preserves the previous reference and allows retry', async (status) => {
    await open()
    const original = copy(win().draft.preInstructionAttachment)
    api.postFormRequest.mockRejectedValueOnce(reject(status, { detail: 'upload denied' }))
    await file()
    expect(q('wp-step-error', child())).toBeTruthy()
    expect(win().draft.preInstructionAttachment).toEqual(original)
    expect(win().uploading).toBe(false)
    expect(api.putRequest).not.toHaveBeenCalled()
    await file()
    expect(win().draft.preInstructionAttachment).toEqual(refFile())
  })

  it.each([
    [409, 'Flow is disposed.', 'disposed'],
    [422, 'Work plan is not editable.', 'not_editable'],
  ] as const)('upload refusal %s locks both windows even when GET still says editable', async (status, detail, reason) => {
    await open()
    await type(q('wp-step-text', child()), 'preserve on refusal')
    api.postFormRequest.mockRejectedValueOnce(reject(status, { detail }))
    await file()
    expect(q('wp-step-lock', child()).dataset.reason).toBe(reason)
    expect(q('wp-edit-lock').dataset.reason).toBe(reason)
    expect(win().draft.preInstructionText).toBe('preserve on refusal')
    expect(win().draft.preInstructionAttachment?.original_filename).toBe('brief.txt')
    expect(saveWorkPlanStep(win())).toBe(false)
    await uploadWorkPlanStepFile(win(), new File(['x'], 'again.txt'))
    expect(api.postFormRequest).toHaveBeenCalledTimes(1)
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('upload 423 refreshes the group lock without losing the previous reference', async () => {
    await open()
    activity = { active: true, run_id: null }
    api.postFormRequest.mockRejectedValueOnce(reject(423, { detail: 'AI running' }))
    await file()
    expect(q('wp-step-lock', child()).dataset.reason).toBe('ai_running')
    expect(win().draft.preInstructionAttachment?.original_filename).toBe('brief.txt')
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('a reviewer picked in the review popover stays visible, marked, once it leaves the registered providers', async () => {
    await open()
    await click(q('wp-edit-step-review', row('c_t1|main')))
    await pick(q('wp-edit-review-ai', q('wp-edit-review-popover')), 'aip_opus')
    answer = { ...answer, registered_providers: [] }
    await refresh()
    const picker = q('wp-edit-review-ai', q('wp-edit-review-popover'))
    expect(inputValue(picker)).toBe('aip_opus')
    expect(picker.textContent).toContain('aip_opus (사용 불가)')
  })

  it('oversized file is rejected before POST, while the exact byte limit is accepted', async () => {
    answer.limits.pre_instruction_attachment_max_bytes = 2
    await open()
    await file('too-large.txt', 3)
    expect(api.postFormRequest).not.toHaveBeenCalled()
    expect(win().issue).toBe('file_limit')
    await file('exact.txt', 2)
    expect(api.postFormRequest).toHaveBeenCalledTimes(1)
  })

  it('a closed parent and reopened parent still wait for the old upload; late response never enters the new draft', async () => {
    await open()
    let resolve!: (value: any) => void
    api.postFormRequest.mockImplementationOnce(() => new Promise(r => { resolve = r }))
    await file()
    expect(disabled(q('wp-step-save', child()))).toBe(true)
    await click(q('wp-edit-close'))
    await confirm()
    expect(workPlanStepPending(DOC)).toBe(true)
    await open()
    await type(q('wp-edit-default-note'), 'new parent')
    expect(disabled(q('wp-edit-save'))).toBe(true)
    resolve({ data: { reference: refFile('late.txt') } })
    await flushPromises()
    expect(win().draft.preInstructionAttachment?.original_filename).toBe('brief.txt')
    expect(workPlanStepPending(DOC)).toBe(false)
    expect(disabled(q('wp-edit-save'))).toBe(false)
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1').pre_instruction_attachment.original_filename).toBe('brief.txt')
  })

  it('deletion during upload drops the late reference; restoring the row never revives the old child', async () => {
    await open('c_t2|main')
    let resolve!: (value: any) => void
    api.postFormRequest.mockImplementationOnce(() => new Promise(r => { resolve = r }))
    await file()
    await click(q('wp-edit-quantity-dec', box()))
    await confirm()
    resolve({ data: { reference: refFile('late.txt') } })
    await flushPromises()
    expect(win().session.target()).toBeNull()
    expect(win().draft.preInstructionAttachment).toBeNull()
    await click(q('wp-edit-quantity-inc', box()))
    expect(win().session.target()).toBeNull()
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('a lock arriving during upload retains the response in the child and prevents apply', async () => {
    await open()
    let resolve!: (value: any) => void
    api.postFormRequest.mockImplementationOnce(() => new Promise(r => { resolve = r }))
    await file()
    lock.value = 'run_locked'
    resolve({ data: { reference: refFile() } })
    await flushPromises()
    expect(win().draft.preInstructionAttachment).toEqual(refFile())
    expect(saveWorkPlanStep(win())).toBe(false)
    expect(api.putRequest).not.toHaveBeenCalled()
    lock.value = null
    await flushPromises()
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1').pre_instruction_attachment).toEqual(refFile())
  })

  it('malformed server references are rejected before changing the child', async () => {
    await open()
    api.postFormRequest.mockResolvedValueOnce({ data: { reference: { ...refFile(), doc_id: 'other' } } })
    await file()
    expect(win().issue).toBe('upload_failed')
    expect(win().draft.preInstructionAttachment?.original_filename).toBe('brief.txt')
    api.postFormRequest.mockResolvedValueOnce({ data: { reference: { ...refFile(), filename: 'ordinary.txt' } } })
    await expect(workPlanUploadInstruction(DOC, 'T#1', new File(['x'], 'x.txt'))).rejects.toThrow()
  })
})

describe('the review popover and the Step window (0073 T#2)', () => {
  const popover = () => q('wp-edit-review-popover')
  async function review(id: string, count: string, reviewer?: string) {
    await click(q('wp-edit-step-review', row(id)))
    await pick(q('wp-edit-review-count', popover()), count)
    if (reviewer !== undefined) await pick(q('wp-edit-review-ai', popover()), reviewer)
    await click(q('wp-edit-review-done', popover()))
  }

  it('a review set while the Step window is dirty survives its save: no conflict, the review is not written back', async () => {
    await open()
    await type(q('wp-step-text', child()), 'child text')
    await review('c_t1|main', '3', 'aip_opus')
    expect(popover()).toBeNull()
    expect(win().draft).toEqual({ preInstructionText: 'child text', preInstructionAttachment: win().baseline.preInstructionAttachment })
    await click(q('wp-step-save', child()))
    expect(workPlanStepWindowList()).toHaveLength(0)
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1')).toMatchObject({ key: 'T#1', review_count: 3, reviewer_provider_id: 'aip_opus', reviewer_provider_display_name: 'Opus', pre_instruction_text: 'child text' })
  })

  it('cancelling the Step window keeps the popover review and drops only the child text', async () => {
    await open()
    await type(q('wp-step-text', child()), 'discard me')
    await review('c_t1|main', '0')
    await click(q('wp-step-cancel', child()))
    await confirm()
    expect(workPlanStepWindowList()).toHaveLength(0)
    expect(q('wp-edit-step-review-summary', row('c_t1|main')).textContent).toBe('검수 없음')
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1')).toMatchObject({ review_count: 0, reviewer_provider_id: null, reviewer_provider_display_name: null, pre_instruction_text: '사전 지시' })
  })

  it('a clean Step window stays clean when the review changes; saving it applies nothing and the review stays', async () => {
    await open('c_t2|main')
    await review('c_t2|main', '2', 'aip_sonnet')
    expect(workPlanStepDirty(win())).toBe(false)
    await click(q('wp-step-save', child()))
    expect(win()).toBeUndefined()
    await pencil('c_t2|main')
    expect(win().draft.preInstructionText).toBeNull()
    await click(q('wp-step-cancel', child()))
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'T#2')).toMatchObject({ key: 'T#2', review_count: 2, reviewer_provider_id: 'aip_sonnet', pre_instruction_text: null })
  })

  it('Esc in the Step window stays the Step window’s: it asks about its unsaved text, the popover behind takes neither the key nor the focus', async () => {
    await open()
    await type(q('wp-step-text', child()), 'child text')
    const reviewButton = q('wp-edit-step-review', row('c_t1|main'))
    const before = q('wp-edit-step-review-summary', row('c_t1|main')).textContent
    await click(reviewButton)
    expect(popover()).not.toBeNull()
    // an Esc in the other window (the popover still open)
    const esc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    q('wp-step-text', child()).dispatchEvent(esc)
    await flushPromises()
    expect(esc.defaultPrevented).toBe(false)
    expect(document.body.querySelector('.ui-confirm-title')).not.toBeNull()
    expect(workPlanStepWindowList()).toHaveLength(1)
    expect(popover()).toBeNull()
    expect(document.activeElement).not.toBe(reviewButton)
    await confirm(false)
    expect(workPlanStepWindowList()).toHaveLength(1)
    expect(win().draft.preInstructionText).toBe('child text')

    // Tab to the other window: the popover closes, the focus stays where it went; that window's Esc is its own
    await click(reviewButton)
    expect(popover()).not.toBeNull()
    const text = q('wp-step-text', child())
    text.focus()
    await flushPromises()
    expect(popover()).toBeNull()
    expect(document.activeElement).toBe(text)
    text.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await flushPromises()
    expect(document.body.querySelector('.ui-confirm-title')).not.toBeNull()
    await confirm()
    expect(workPlanStepWindowList()).toHaveLength(0)
    // the plan window and its review are untouched
    expect(q('wp-edit-step-review-summary', row('c_t1|main')).textContent).toBe(before)
  })

  it('a result row’s review is set in the popover while its Step window is open; that window applies nothing', async () => {
    await open('c_t1|result')
    await review('c_t1|result', '-1', 'aip_sonnet')
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'TR#1')).toMatchObject({ key: 'TR#1', review_count: -1, reviewer_provider_id: 'aip_sonnet', pre_instruction_text: null, pre_instruction_attachment: null })
  })
})

describe('the attachment area: drop, replace, remove and the moved notes (0073 T#3)', () => {
  // jsdom has no DragEvent / DataTransfer: a plain cancelable event carrying the fields the handlers read.
  function dragEvent(type: string, files: File[], types: string[]) {
    const event = new Event(type, { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'dataTransfer', { value: { types, files, dropEffect: 'none' } })
    return event as Event & { dataTransfer: { dropEffect: string } }
  }
  async function drop(files: File[], types: string[] = files.length ? ['Files'] : ['text/plain']) {
    const zone = q('wp-step-drop', child())
    const over = dragEvent('dragover', files, types)
    zone.dispatchEvent(over)
    await flushPromises()
    const dragging = zone.classList.contains('is-drag-over')
    const event = dragEvent('drop', files, types)
    zone.dispatchEvent(event)
    await flushPromises()
    return { over, event, dragging }
  }
  const bytes = (name: string, size = 2) => new File(['x'.repeat(size)], name)
  const error = () => q('wp-step-error', child())
  const choose = () => q('wp-step-choose', child())

  it('a dropped file takes the picker’s upload with the stored key; the area shows icon, name, replace and remove', async () => {
    await open()
    expect(child().querySelector('.step-file-icon svg path')!.getAttribute('d')).toBeTruthy()
    expect(q('wp-step-file-name', child()).textContent).toBe('brief.txt')
    expect(choose().textContent!.trim()).toBe('교체')
    expect(q('wp-step-remove', child()).textContent!.trim()).toBe('삭제')
    const { over, event, dragging } = await drop([bytes('dropped.txt')])
    expect(over.defaultPrevented).toBe(true)
    expect(over.dataTransfer.dropEffect).toBe('copy')
    expect(dragging).toBe(true)
    expect(event.defaultPrevented).toBe(true)
    expect(q('wp-step-drop', child()).classList.contains('is-drag-over')).toBe(false)
    expect(api.postFormRequest).toHaveBeenCalledTimes(1)
    expect(api.postFormRequest.mock.calls[0][0]).toBe(WP + '/pre-instruction-attachments')
    const form = api.postFormRequest.mock.calls[0][1] as FormData
    expect(form.get('step_key')).toBe('T#1')
    expect((form.get('file') as File).name).toBe('dropped.txt')
    expect(q('wp-step-file-name', child()).textContent).toBe('dropped.txt')
    expect(api.putRequest).not.toHaveBeenCalled()
    // replace by a second drop, then by the picker: the last one is the draft's
    await drop([bytes('second.txt')])
    await file('picked.txt')
    expect(api.postFormRequest).toHaveBeenCalledTimes(3)
    expect(q('wp-step-file-name', child()).textContent).toBe('picked.txt')
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1').pre_instruction_attachment).toEqual(refFile('picked.txt'))
    expect(reserved).toEqual(new Set([refFile('picked.txt').filename]))
    // remove: the area goes back to its empty state; the parent PUT clears the reference
    await pencil('c_t1|main')
    await click(q('wp-step-remove', child()))
    expect(q('wp-step-file-name', child())).toBeNull()
    expect(q('wp-step-file-empty', child()).textContent).toBe('파일을 끌어 놓거나 선택하세요')
    expect(choose().textContent!.trim()).toBe('파일 선택')
    expect(q('wp-step-remove', child())).toBeNull()
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'T#1').pre_instruction_attachment).toBeNull()
    expect(reserved.size).toBe(0)
  })

  it('a dropped file over the size limit is refused before POST and the error names the limit; the exact limit uploads', async () => {
    answer.limits.pre_instruction_attachment_max_bytes = 2
    await open()
    await drop([bytes('too-large.txt', 3)])
    expect(api.postFormRequest).not.toHaveBeenCalled()
    expect(win().issue).toBe('file_limit')
    expect(error().dataset.issue).toBe('file_limit')
    expect(error().textContent).toContain('첨부 파일 크기 제한(2 B)을 초과했습니다.')
    expect(win().draft.preInstructionAttachment?.original_filename).toBe('brief.txt')
    await drop([bytes('exact.txt', 2)])
    expect(api.postFormRequest).toHaveBeenCalledTimes(1)
    expect(q('wp-step-error', child())).toBeNull()
    expect(q('wp-step-file-name', child()).textContent).toBe('exact.txt')
  })

  it('a result step has no drop area and takes no dropped file', async () => {
    await open('c_t1|result')
    expect(q('wp-step-drop', child())).toBeNull()
    expect(q('wp-step-attachment-info', child())).toBeNull()
    await dropWorkPlanStepFiles(win(), [bytes('no.txt')])
    await dropWorkPlanStepFiles(win(), [bytes('a.txt'), bytes('b.txt')])
    expect(api.postFormRequest).not.toHaveBeenCalled()
    expect(win().issue).toBeNull()
    expect(win().draft.preInstructionAttachment).toBeNull()
  })

  it('a new card’s drop is refused with the save-first error until the parent is saved; then it uploads with the new key', async () => {
    await open()
    await click(q('wp-step-cancel', child()))
    await click(q('wp-edit-quantity-inc', box()))
    await click(q('wp-edit-step-edit', document.querySelector<HTMLElement>('[data-test="wp-edit-step"][data-step="T#3"]')!))
    expect(q('wp-step-file-empty', child()).textContent).toBe('작업계획 저장 후 첨부할 수 있습니다')
    expect(disabled(choose())).toBe(true)
    const { over, dragging } = await drop([bytes('early.txt')])
    expect(over.defaultPrevented).toBe(true)
    expect(dragging).toBe(false)
    expect(api.postFormRequest).not.toHaveBeenCalled()
    expect(error().dataset.issue).toBe('save_parent_first')
    expect(error().textContent).toContain('새 단계의 첨부는 작업계획을 저장한 후 등록할 수 있습니다.')
    await click(q('wp-edit-save'))
    expect(win().session.attachmentStepKey()).toBe('T#3')
    expect(q('wp-step-file-empty', child()).textContent).toBe('파일을 끌어 놓거나 선택하세요')
    await drop([bytes('later.txt')])
    expect((api.postFormRequest.mock.calls[0][1] as FormData).get('step_key')).toBe('T#3')
    expect(q('wp-step-file-name', child()).textContent).toBe('later.txt')
  })

  it.each(['read_only', 'run_locked'] as const)('a %s lock refuses the drop and keeps the draft', async (reason) => {
    await open()
    lock.value = reason
    await flushPromises()
    const { over, dragging } = await drop([bytes('locked.txt')])
    expect(over.dataTransfer.dropEffect).toBe('none')
    expect(dragging).toBe(false)
    expect(api.postFormRequest).not.toHaveBeenCalled()
    expect(win().draft.preInstructionAttachment?.original_filename).toBe('brief.txt')
    expect(disabled(q('wp-step-remove', child()))).toBe(true)
  })

  it('an upload in flight refuses another drop', async () => {
    await open()
    let resolve!: (value: any) => void
    api.postFormRequest.mockImplementationOnce(() => new Promise(r => { resolve = r }))
    await drop([bytes('first.txt')])
    expect(choose().textContent!.trim()).toBe('업로드 중…')
    const { over } = await drop([bytes('second.txt')])
    expect(over.dataTransfer.dropEffect).toBe('none')
    expect(api.postFormRequest).toHaveBeenCalledTimes(1)
    resolve({ data: { reference: refFile('first.txt') } })
    await flushPromises()
    expect(q('wp-step-file-name', child()).textContent).toBe('first.txt')
  })

  it('several files are refused with one message; a drag without files (a card, text) is not taken', async () => {
    await open()
    await drop([bytes('a.txt'), bytes('b.txt')])
    expect(api.postFormRequest).not.toHaveBeenCalled()
    expect(error().dataset.issue).toBe('one_file')
    expect(error().textContent).toContain('파일은 하나만 첨부할 수 있습니다.')
    expect(win().draft.preInstructionAttachment?.original_filename).toBe('brief.txt')
    await type(q('wp-step-text', child()), 'clears the error')
    expect(q('wp-step-error', child())).toBeNull()
    const { over, event, dragging } = await drop([], ['text/plain'])
    expect(over.defaultPrevented).toBe(false)
    expect(event.defaultPrevented).toBe(false)
    expect(dragging).toBe(false)
    expect(api.postFormRequest).not.toHaveBeenCalled()
    expect(q('wp-step-error', child())).toBeNull()
  })

  it.each([
    [500, 'server error', undefined],
    [422, 'Work plan is not editable.', 'not_editable'],
  ] as const)('a dropped upload failing with %s is classified as the picker’s (upload_failed, lock refresh)', async (status, detail, reason) => {
    await open()
    api.postFormRequest.mockRejectedValueOnce(reject(status, { detail }))
    await drop([bytes('fails.txt')])
    expect(win().issue).toBe('upload_failed')
    expect(win().draft.preInstructionAttachment?.original_filename).toBe('brief.txt')
    if (reason) expect(q('wp-step-lock', child()).dataset.reason).toBe(reason)
    else expect(q('wp-step-lock', child())).toBeNull()
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('the long notes are tooltips, not text; the checks they describe still hold', async () => {
    await open()
    const text = child().textContent!
    for (const note of ['파일은 즉시 업로드됩니다', '이 단계만 작업계획 편집에 반영합니다', 'bytes', '저장한 후 등록할 수 있습니다']) expect(text).not.toContain(note)
    const info = q('wp-step-attachment-info', child())
    expect(info.getAttribute('aria-label')).toBe('첨부 안내')
    expect(info.getAttribute('title')).toBe('파일 1개, 최대 1 MB. 파일은 즉시 업로드됩니다. 취소·교체·제거된 파일은 다음 작업계획 저장 성공 시 정리됩니다.')
    expect(q('wp-step-save', child()).getAttribute('title')).toBe('이 단계만 작업계획 편집에 반영합니다. 서버 저장은 부모 작업계획의 [저장]에서 합니다.')
    // the limit in the tooltip is the one the check uses
    await drop([bytes('big.txt', 1048577)])
    expect(win().issue).toBe('file_limit')
    expect(error().textContent).toContain('(1 MB)')
    expect(api.postFormRequest).not.toHaveBeenCalled()
    // a new card's tooltip starts with the save-first rule
    await click(q('wp-step-cancel', child()))
    await click(q('wp-edit-quantity-inc', box()))
    await click(q('wp-edit-step-edit', document.querySelector<HTMLElement>('[data-test="wp-edit-step"][data-step="T#3"]')!))
    expect(q('wp-step-attachment-info', child()).getAttribute('title')).toMatch(/^새 단계의 첨부는 작업계획을 저장한 후 등록할 수 있습니다\. 파일 1개, 최대 1 MB\./)
  })

  it('a fractional size limit reads as such in the tooltip and the error', async () => {
    answer.limits.pre_instruction_attachment_max_bytes = 1536
    await open()
    expect(q('wp-step-attachment-info', child()).getAttribute('title')).toContain('파일 1개, 최대 1.5 KB.')
    await drop([bytes('over.txt', 1537)])
    expect(error().textContent).toContain('(1.5 KB)')
    expect(api.postFormRequest).not.toHaveBeenCalled()
  })

  it('no size limit: the tooltip names none and any size is dropped', async () => {
    answer.limits.pre_instruction_attachment_max_bytes = 0
    await open()
    expect(q('wp-step-attachment-info', child()).getAttribute('title')).not.toContain('최대')
    await drop([bytes('any-size.txt', 4096)])
    expect(api.postFormRequest).toHaveBeenCalledTimes(1)
  })
})
