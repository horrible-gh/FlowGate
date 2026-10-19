// 0072 T#2: real parent/child ModelessShells, mocked HTTP at the transport boundary.
// Reserved-file state models the existing successful-PUT cleanup contract; no server writes.
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { closeAllDocumentAiWindows } from '@/features/documentWindow'
import { documentIoConflict } from '@/features/documentWindow/documentEditRegistry'
import { closeAllWorkPlanEditWindows, openWorkPlanEditWindow, WorkPlanEditWindows } from '@/features/workPlanWindow'
import { workPlanUploadInstruction } from '@/features/document'
import { saveWorkPlanStep, setWorkPlanStepValue, uploadWorkPlanStepFile, workPlanStepWindowList, workPlanStepPending } from '@/features/workPlanWindow/workPlanStepEditWindows'
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
    expect(lastBody!.steps[0].note).toBe('parent note')
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

  it('review counts follow the server; zero clears the reviewer; result steps only edit review', async () => {
    await open('c_t1|result')
    expect(q('wp-step-text', child())).toBeNull()
    expect(q('wp-step-file', child())).toBeNull()
    expect(q('wp-step-result', child())).toBeTruthy()
    expect([...((q('wp-step-review', child())) as HTMLSelectElement).options].map(o => o.value)).toEqual(['0', '1', '2', '3', '-1'])
    await pick(q('wp-step-review', child()), '-1')
    await pick(q('wp-step-reviewer', child()), 'aip_sonnet')
    await pick(q('wp-step-review', child()), '0')
    expect(disabled(q('wp-step-reviewer', child()))).toBe(true)
    expect(win().draft.reviewerProviderId).toBeNull()
    setWorkPlanStepValue(win(), 'preInstructionText', 'forbidden')
    await uploadWorkPlanStepFile(win(), new File(['x'], 'no.txt'))
    expect(win().draft.preInstructionText).toBeNull()
    expect(api.postFormRequest).not.toHaveBeenCalled()
    await click(q('wp-step-save', child()))
    await click(q('wp-edit-save'))
    expect(lastBody!.steps.find((s: any) => s.key === 'TR#1')).toMatchObject({ review_count: 0, reviewer_provider_id: null, pre_instruction_text: null, pre_instruction_attachment: null })
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
    expect(lastBody!.steps[1].pre_instruction_text).toBe('😀가')
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
    expect(q('wp-edit-revision').textContent).toBe('r7')
    expect(api.putRequest).not.toHaveBeenCalled()
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
    expect(lastBody!.steps[1].pre_instruction_text).toBe('keep while locked')
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
    expect(lastBody!.steps[1].pre_instruction_text).toBe('survives failed save')
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
    expect(lastBody!.steps[1].pre_instruction_attachment).toBeNull()
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
    expect(lastBody!.steps[1].pre_instruction_attachment).toEqual(original)
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

  it('a selected reviewer remains visible if removed from the registered providers while the child is dirty', async () => {
    await open()
    await pick(q('wp-step-reviewer', child()), 'aip_opus')
    answer = { ...answer, registered_providers: [] }
    await refresh()
    expect(inputValue(q('wp-step-reviewer', child()))).toBe('aip_opus')
    expect(q('wp-step-reviewer', child()).textContent).toContain('aip_opus (사용 불가)')
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
    expect(lastBody!.steps[1].pre_instruction_attachment.original_filename).toBe('brief.txt')
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
    expect(lastBody!.steps[1].pre_instruction_attachment).toEqual(refFile())
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
