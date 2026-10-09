/**
 * flowgate.v02.0048 T#8 — the attachments tab with `@/features/document` mocked as a small server that
 * also refuses (D#1 §12.1): the attachments of the target document, open (download handed to the
 * browser's save), ＋ add (multipart, client checks against the server limits), delete with a confirm
 * step, the list and the guard read again after every change, changes closed until that read is back;
 * the refusals — 409 DOCUMENT_NOT_MUTABLE (by reason) and 423 shown as a reason that keeps adding and
 * deleting disabled until a guard event, 409 ATTACHMENT_EXISTS, 404, 403, 500, network — the guard
 * (AI running, disposed), the read-only Flows, a failed read with retry and a Node change (an
 * instruction and its report each show their own attachments). Last, the real registry: the
 * attachments tab with its number in the Frame (the number follows this tab's add / delete), and no tab
 * on a Node without a document.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import AttachmentsTab from '@/features/designer/inspector/features/attachments/AttachmentsTab.vue'
import { ATTACHMENTS_EVENTS, ATTACHMENTS_GUARD_EVENTS } from '@/features/designer/inspector/features/attachments/useAttachments'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { SELECTIONS, V01_GRAPH, V01_TASK_NODE, prepareInspectorI18n, selectionOf, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'

// The calls are mocked; normalization and error reading stay real.
const listCall = vi.fn()
const uploadCall = vi.fn()
const deleteCall = vi.fn()
const downloadCall = vi.fn()
const activityRead = vi.fn()
const documentRead = vi.fn()
const detailRead = vi.fn()
const relationsRead = vi.fn()
const qaReadCall = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    attachmentsRead: (...args: unknown[]) => listCall(...args),
    attachmentsUpload: (...args: unknown[]) => uploadCall(...args),
    attachmentsDelete: (...args: unknown[]) => deleteCall(...args),
    attachmentsDownload: (...args: unknown[]) => downloadCall(...args),
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    reviewDocumentRead: (...args: unknown[]) => documentRead(...args),
    // The documents tab, the header actions and the Q&A tab share the Frame in the registry cases.
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
    qaRead: (...args: unknown[]) => qaReadCall(...args),
  }
})

const { toAttachmentsList, toAttachmentsUploadResult, toAttachmentsDeleteResult, toReviewActivity, toReviewDocument, toDocumentDetail, toQaThread } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const V01_DOC = V01_TASK_NODE.document!.docId
const V01_FLOW = V01_GRAPH.flowId
const OTHER_DOC = 'flowgate.v02.0048.0013-TR'

// --- The server ------------------------------------------------------------------------------------

interface ServerRow {
  doc_id: string
  original_filename: string
  filename: string
  size: number
  content_type: string
  uploaded_by: string
  uploaded_at: string
}

const rows = new Map<string, ServerRow[]>()
let running = false
let disposed = false
/** The next call of that name is this refusal (once). */
const refusals = new Map<string, unknown>()

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
const envelope = (status: number, code: string, details: Record<string, unknown> = {}) =>
  axiosError(status, { error: { code, message: 'server text', details } })
const networkError = () => ({ isAxiosError: true, message: 'Network Error' })
const notMutable = (reason?: string) => envelope(409, 'DOCUMENT_NOT_MUTABLE', reason ? { reason } : {})
const locked = () => axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED', message: 'm', run_live: true } })

function refusalOf(call: string): unknown {
  const refusal = refusals.get(call)
  refusals.delete(call)
  return refusal
}

const row = (docId: string, filename: string, size: number, original = filename): ServerRow => ({
  doc_id: docId,
  original_filename: original,
  filename,
  size,
  content_type: 'application/octet-stream',
  uploaded_by: 'u1',
  uploaded_at: '2026-10-09T10:00:00Z',
})

function seed() {
  rows.set(V01_DOC, [row(V01_DOC, '요구사항.xlsx', 18342), row(V01_DOC, '요구사항 (1).xlsx', 512, '요구사항.xlsx')])
  rows.set(OTHER_DOC, [row(OTHER_DOC, 'report.pdf', 3 * 1024 * 1024)])
}

const listOf = (docId: string) => ({ data: { doc_id: docId, attachments: [...(rows.get(docId) ?? [])], count: rows.get(docId)?.length ?? 0 } })

/** The server's mutability guard: disposed answers 409 from the route, a live AI run 423 from the policy. */
function guardRefusal(): unknown {
  if (disposed) return notMutable('group_disposed')
  if (running) return locked()
  return null
}

function serve() {
  listCall.mockImplementation(async (docId: string) => {
    const refusal = refusalOf('read')
    if (refusal) throw refusal
    return toAttachmentsList(listOf(docId))
  })
  uploadCall.mockImplementation(async (docId: string, files: File[]) => {
    const refusal = refusalOf('upload') ?? guardRefusal()
    if (refusal) throw refusal
    const added = files.map((file) => row(docId, file.name, file.size))
    rows.set(docId, [...(rows.get(docId) ?? []), ...added])
    return toAttachmentsUploadResult({ data: { doc_id: docId, attachments: added, count: added.length } })
  })
  deleteCall.mockImplementation(async (docId: string, filename: string) => {
    const refusal = refusalOf('delete') ?? guardRefusal()
    if (refusal) throw refusal
    const list = rows.get(docId) ?? []
    if (!list.some((entry) => entry.filename === filename)) throw envelope(404, 'ATTACHMENT_NOT_FOUND')
    rows.set(docId, list.filter((entry) => entry.filename !== filename))
    return toAttachmentsDeleteResult({ data: { doc_id: docId, filename, file_deleted: true, metadata_deleted: true, deleted_at: 't' } })
  })
  downloadCall.mockImplementation(async (docId: string, filename: string) => {
    const refusal = refusalOf('download')
    if (refusal) throw refusal
    if (!(rows.get(docId) ?? []).some((entry) => entry.filename === filename)) throw envelope(404, 'ATTACHMENT_NOT_FOUND')
    return { filename, blob: new Blob([`content of ${filename}`]) }
  })
  activityRead.mockImplementation(async (groupId: string) =>
    toReviewActivity(running ? { active: true, run_id: 'run_1', status: 'running', group_id: groupId } : { active: false, group_id: groupId }),
  )
  documentRead.mockImplementation(async (docId: string) => toReviewDocument(docRow(docId)))
  detailRead.mockImplementation(async (docId: string) => toDocumentDetail(docRow(docId)))
  relationsRead.mockImplementation(async () => [])
  qaReadCall.mockImplementation(async (docId: string) => toQaThread({ ok: true, qa: { doc_id: docId, status: null, items: [] } }))
}

const docRow = (docId: string) => ({ doc_id: docId, group_id: docId.split('.').slice(0, 3).join('.'), type_code: 'TR', title: docId, status: 'open', doc_review_status: 'approved', group_disposed: disposed })

// --- The browser's save ----------------------------------------------------------------------------

const saved: { href: string; download: string }[] = []
const realCreate = URL.createObjectURL
const realRevoke = URL.revokeObjectURL

// --- Mounting --------------------------------------------------------------------------------------

let pinia: Pinia
let stream: ProjectEventStream
let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  stream = installProjectEventStream()
  rows.clear()
  refusals.clear()
  running = false
  disposed = false
  for (const mock of [listCall, uploadCall, deleteCall, downloadCall, activityRead, documentRead, detailRead, relationsRead, qaReadCall]) mock.mockReset()
  seed()
  serve()
  saved.length = 0
  URL.createObjectURL = vi.fn(() => 'blob:fake/1')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved.push({ href: this.getAttribute('href') ?? '', download: this.download })
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  vi.restoreAllMocks()
  URL.createObjectURL = realCreate
  URL.revokeObjectURL = realRevoke
  vi.useRealTimers()
})

async function mountTab(target: InspectorTarget) {
  wrapper = mount(AttachmentsTab, { props: { target }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

const v01Target = (fields: Partial<InspectorTarget> = {}): InspectorTarget => ({ ...targetOf(SELECTIONS.v01), ...fields })
const otherTarget = () => v01Target({ docId: OTHER_DOC, docType: 'TR', nodeId: `doc:${OTHER_DOC}` })

const names = (w: VueWrapper<any>) => w.findAll('[data-test="attachments-item"]').map((el) => el.attributes('data-name'))
const itemEl = (w: VueWrapper<any>, name: string) => w.find(`[data-test="attachments-item"][data-name="${name}"]`)
const addButton = (w: VueWrapper<any>) => w.find('[data-test="attachments-add"]')
const deleteButton = (w: VueWrapper<any>, name: string) => itemEl(w, name).find('[data-test="attachments-delete"]')
const isDisabled = (el: ReturnType<VueWrapper<any>['find']>) => el.attributes('disabled') !== undefined
const reasonText = (w: VueWrapper<any>) => w.find('[data-test="attachments-reason"]').text()
const failureText = (w: VueWrapper<any>) => w.find('[data-test="attachments-failure"]').text()

async function pick(w: VueWrapper<any>, files: File[]) {
  const input = w.find('[data-test="attachments-picker"]')
  Object.defineProperty(input.element, 'files', { value: files, configurable: true })
  await input.trigger('change')
  await flushPromises()
}

async function removeByConfirm(w: VueWrapper<any>, name: string) {
  await deleteButton(w, name).trigger('click')
  await itemEl(w, name).find('[data-test="attachments-delete-yes"]').trigger('click')
  await flushPromises()
}

const file = (name: string, size = 4) => new File(['x'.repeat(Math.min(size, 16))], name, { type: 'text/plain' })
const bigFile = (name: string) => {
  const f = file(name)
  Object.defineProperty(f, 'size', { value: 20 * 1024 * 1024 + 1 })
  return f
}

// --- Reading ---------------------------------------------------------------------------------------

describe('attachments tab — reading (flowgate.v02.0048 T#8)', () => {
  it('reads the target document once and shows its attachments in the server order with their sizes', async () => {
    const w = await mountTab(v01Target())
    expect(listCall).toHaveBeenCalledTimes(1)
    expect(listCall).toHaveBeenCalledWith(V01_DOC)
    expect(names(w)).toEqual(['요구사항.xlsx', '요구사항 (1).xlsx'])
    expect(itemEl(w, '요구사항.xlsx').find('[data-test="attachments-size"]').text()).toBe('17.9KB')
    expect(itemEl(w, '요구사항 (1).xlsx').find('[data-test="attachments-original"]').text()).toBe('올린 이름: 요구사항.xlsx')
    expect(itemEl(w, '요구사항.xlsx').find('[data-test="attachments-original"]').exists()).toBe(false)
    expect(isDisabled(addButton(w))).toBe(false)
    expect(w.text()).toContain('파일당 최대 20MB')
    expect(w.find('[data-test="attachments-reason"]').exists()).toBe(false)
  })

  it('a document without attachments shows the empty state; adding is still offered', async () => {
    rows.set(V01_DOC, [])
    const w = await mountTab(v01Target())
    expect(w.find('[data-test="inspector-pane-state"][data-state="empty"]').text()).toContain('이 문서에 첨부된 파일이 없습니다.')
    expect(isDisabled(addButton(w))).toBe(false)
  })

  it.each([
    ['403', envelope(403, 'FORBIDDEN'), '이 문서의 첨부를 볼 권한이 없습니다.'],
    ['404', envelope(404, 'DOCUMENT_NOT_FOUND'), '문서를 찾을 수 없습니다.'],
    ['network', networkError(), '서버에 연결하지 못했습니다.'],
    ['500', envelope(500, 'ATTACHMENT_OPERATION_FAILED'), '첨부 목록을 불러오지 못했습니다.'],
  ])('a failed read (%s) shows why with a retry that reads again', async (_label, refusal, text) => {
    refusals.set('read', refusal)
    const w = await mountTab(v01Target())
    expect(w.find('[data-test="inspector-pane-state"][data-state="error"]').text()).toContain(text)
    await w.find('[data-test="inspector-pane-retry"]').trigger('click')
    await flushPromises()
    expect(listCall).toHaveBeenCalledTimes(2)
    expect(names(w)).toEqual(['요구사항.xlsx', '요구사항 (1).xlsx'])
  })

  it('subscribes to the guard events only (attachment changes send no server event)', () => {
    expect(ATTACHMENTS_EVENTS).toEqual([])
    expect([...ATTACHMENTS_GUARD_EVENTS]).toEqual(['ai_invoke_started', 'ai_invoke_finished', 'document_explorer_refresh'])
  })
})

// --- Open, add, delete -----------------------------------------------------------------------------

describe('attachments tab — open, add, delete (flowgate.v02.0048 T#8)', () => {
  it('open downloads the file by its stored name and hands it to the browser to save', async () => {
    const w = await mountTab(v01Target())
    await itemEl(w, '요구사항 (1).xlsx').find('[data-test="attachments-open"]').trigger('click')
    await flushPromises()
    expect(downloadCall).toHaveBeenCalledWith(V01_DOC, '요구사항 (1).xlsx')
    expect(saved).toEqual([{ href: 'blob:fake/1', download: '요구사항 (1).xlsx' }])
    expect(document.querySelectorAll('a[download]')).toHaveLength(0)
    expect(listCall).toHaveBeenCalledTimes(1)
  })

  it('open of a file that is gone reads the list again and says why above it (its row is gone)', async () => {
    const w = await mountTab(v01Target())
    rows.set(V01_DOC, [rows.get(V01_DOC)![0]])
    await itemEl(w, '요구사항 (1).xlsx').find('[data-test="attachments-open"]').trigger('click')
    await flushPromises()
    expect(saved).toEqual([])
    expect(listCall).toHaveBeenCalledTimes(2)
    expect(names(w)).toEqual(['요구사항.xlsx'])
    expect(failureText(w)).toBe('첨부나 문서를 찾을 수 없습니다. 목록을 다시 읽었습니다.')
    expect(w.find('[data-test="attachments-item-failure"]').exists()).toBe(false)
  })

  it('open refused while the row stays (403) says why on that row', async () => {
    refusals.set('download', envelope(403, 'FORBIDDEN'))
    const w = await mountTab(v01Target())
    await itemEl(w, '요구사항.xlsx').find('[data-test="attachments-open"]').trigger('click')
    await flushPromises()
    expect(itemEl(w, '요구사항.xlsx').find('[data-test="attachments-item-failure"]').text()).toBe('권한이 없어 거절되었습니다.')
    expect(w.find('[data-test="attachments-failure"]').exists()).toBe(false)
  })

  it('open that cannot reach the server shows why on that row; the list stays', async () => {
    refusals.set('download', networkError())
    const w = await mountTab(v01Target())
    await itemEl(w, '요구사항.xlsx').find('[data-test="attachments-open"]').trigger('click')
    await flushPromises()
    expect(itemEl(w, '요구사항.xlsx').find('[data-test="attachments-item-failure"]').text()).toBe('서버에 연결하지 못했습니다.')
    expect(listCall).toHaveBeenCalledTimes(1)
  })

  it('add uploads the picked files to the target document, then reads the list and the guard again', async () => {
    const w = await mountTab(v01Target())
    expect([listCall.mock.calls.length, activityRead.mock.calls.length, documentRead.mock.calls.length]).toEqual([1, 1, 1])
    await pick(w, [file('a.txt'), file('b.png')])
    expect(uploadCall).toHaveBeenCalledTimes(1)
    const [docId, files] = uploadCall.mock.calls[0]
    expect(docId).toBe(V01_DOC)
    expect((files as File[]).map((f) => f.name)).toEqual(['a.txt', 'b.png'])
    expect([listCall.mock.calls.length, activityRead.mock.calls.length, documentRead.mock.calls.length]).toEqual([2, 2, 2])
    expect(names(w)).toEqual(['요구사항.xlsx', '요구사항 (1).xlsx', 'a.txt', 'b.png'])
    expect(w.find('[data-test="attachments-failure"]').exists()).toBe(false)
    expect((w.find('[data-test="attachments-picker"]').element as HTMLInputElement).value).toBe('')
  })

  it('the add button opens the file picker', async () => {
    const w = await mountTab(v01Target())
    const clicked = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    await addButton(w).trigger('click')
    expect(clicked).toHaveBeenCalledTimes(1)
  })

  it('while a change waits for the server and for the read after it, adding and deleting are closed', async () => {
    let releaseUpload: () => void = () => {}
    uploadCall.mockImplementationOnce(
      (docId: string, files: File[]) =>
        new Promise((resolve) => {
          releaseUpload = () => {
            const added = files.map((f) => row(docId, f.name, f.size))
            rows.set(docId, [...rows.get(docId)!, ...added])
            resolve(toAttachmentsUploadResult({ data: { doc_id: docId, attachments: added } }))
          }
        }),
    )
    const w = await mountTab(v01Target())
    await pick(w, [file('a.txt')])
    expect(isDisabled(addButton(w))).toBe(true)
    expect(isDisabled(deleteButton(w, '요구사항.xlsx'))).toBe(true)
    expect(reasonText(w)).toBe('첨부 변경을 처리하는 중입니다.')
    // The read after the upload is held: still closed, the old list cannot send the same upload twice.
    let releaseRead: () => void = () => {}
    listCall.mockImplementationOnce((docId: string) => new Promise((resolve) => (releaseRead = () => resolve(toAttachmentsList(listOf(docId))))))
    releaseUpload()
    await flushPromises()
    expect(isDisabled(addButton(w))).toBe(true)
    await pick(w, [file('a.txt')])
    expect(uploadCall).toHaveBeenCalledTimes(1)
    releaseRead()
    await flushPromises()
    expect(isDisabled(addButton(w))).toBe(false)
    expect(names(w)).toEqual(['요구사항.xlsx', '요구사항 (1).xlsx', 'a.txt'])
    expect(rows.get(V01_DOC)!.filter((entry) => entry.filename === 'a.txt')).toHaveLength(1)
  })

  it.each([
    ['a file over 20 MiB', () => [file('ok.txt'), bigFile('big.bin')], 'big.bin 파일이 20MB를 넘어 올리지 않았습니다.'],
    ['21 files at once', () => Array.from({ length: 21 }, (_, i) => file(`f${i}.txt`)), '한 번에 20개까지 올릴 수 있습니다.'],
  ])('%s is not sent and says why', async (_label, files, text) => {
    const w = await mountTab(v01Target())
    await pick(w, files())
    expect(uploadCall).not.toHaveBeenCalled()
    expect(failureText(w)).toBe(text)
  })

  it('a document at the maximum of 200 attachments: adding is disabled with the limit, deleting stays', async () => {
    rows.set(V01_DOC, Array.from({ length: 200 }, (_, i) => row(V01_DOC, `f${i}.txt`, 1)))
    const w = await mountTab(v01Target())
    expect(isDisabled(addButton(w))).toBe(true)
    expect(reasonText(w)).toBe('문서당 첨부 한도(200개)에 도달했습니다.')
    expect(isDisabled(deleteButton(w, 'f0.txt'))).toBe(false)
  })

  it('delete asks first; cancel sends nothing', async () => {
    const w = await mountTab(v01Target())
    await deleteButton(w, '요구사항.xlsx').trigger('click')
    expect(itemEl(w, '요구사항.xlsx').find('[data-test="attachments-confirm"]').text()).toContain('요구사항.xlsx 파일을 삭제할까요?')
    await itemEl(w, '요구사항.xlsx').find('[data-test="attachments-delete-no"]').trigger('click')
    expect(itemEl(w, '요구사항.xlsx').find('[data-test="attachments-confirm"]').exists()).toBe(false)
    expect(deleteCall).not.toHaveBeenCalled()
  })

  it('delete removes the attachment by its stored name, then reads the list and the guard again', async () => {
    const w = await mountTab(v01Target())
    await removeByConfirm(w, '요구사항 (1).xlsx')
    expect(deleteCall).toHaveBeenCalledWith(V01_DOC, '요구사항 (1).xlsx')
    expect([listCall.mock.calls.length, activityRead.mock.calls.length]).toEqual([2, 2])
    expect(names(w)).toEqual(['요구사항.xlsx'])
  })

  it('delete of an attachment already gone reads the list again and says why above it', async () => {
    const w = await mountTab(v01Target())
    rows.set(V01_DOC, [rows.get(V01_DOC)![1]])
    await removeByConfirm(w, '요구사항.xlsx')
    expect(listCall).toHaveBeenCalledTimes(2)
    expect(names(w)).toEqual(['요구사항 (1).xlsx'])
    expect(failureText(w)).toBe('첨부나 문서를 찾을 수 없습니다. 목록을 다시 읽었습니다.')
  })

  it.each([
    ['403', 'delete', envelope(403, 'FORBIDDEN'), '권한이 없어 거절되었습니다.'],
    ['500', 'delete', envelope(500, 'ATTACHMENT_DELETE_FAILED'), '서버에서 처리하지 못했습니다. 원인은 서버 로그에 남았습니다.'],
    ['network', 'delete', networkError(), '서버에 연결하지 못했습니다.'],
  ])('a delete refused with %s shows why on that row; deleting stays possible', async (_label, call, refusal, text) => {
    refusals.set(call, refusal)
    const w = await mountTab(v01Target())
    await removeByConfirm(w, '요구사항.xlsx')
    expect(itemEl(w, '요구사항.xlsx').find('[data-test="attachments-item-failure"]').text()).toBe(text)
    expect(listCall).toHaveBeenCalledTimes(2)
    expect(isDisabled(deleteButton(w, '요구사항.xlsx'))).toBe(false)
  })

  it.each([
    ['409 ATTACHMENT_EXISTS', envelope(409, 'ATTACHMENT_EXISTS', { reason: 'per_document_limit', limit: 200 }), '문서당 첨부 한도를 넘어 거절되었습니다.'],
    ['413', envelope(413, 'ATTACHMENT_TOO_LARGE'), '파일이 너무 커서 거절되었습니다.'],
    ['400 INVALID_FILENAME', envelope(400, 'INVALID_FILENAME'), '파일 이름이나 요청이 올바르지 않아 거절되었습니다.'],
    ['500', envelope(500, 'ATTACHMENT_STORE_FAILED', { reason: 'storage' }), '서버에서 처리하지 못했습니다. 원인은 서버 로그에 남았습니다.'],
    ['network', networkError(), '서버에 연결하지 못했습니다.'],
  ])('an upload refused with %s shows why and reads the list again; adding stays possible', async (_label, refusal, text) => {
    refusals.set('upload', refusal)
    const w = await mountTab(v01Target())
    await pick(w, [file('a.txt')])
    expect(failureText(w)).toBe(text)
    expect(listCall).toHaveBeenCalledTimes(2)
    expect(names(w)).toEqual(['요구사항.xlsx', '요구사항 (1).xlsx'])
    expect(isDisabled(addButton(w))).toBe(false)
  })
})

// --- 409 DOCUMENT_NOT_MUTABLE and the guard ---------------------------------------------------------

describe('attachments tab — not mutable (409 DOCUMENT_NOT_MUTABLE / 423) and the guard (flowgate.v02.0048 T#8)', () => {
  it('409 group_ai_running on add: the refusal is shown, adding and deleting are disabled with the reason, opening still works', async () => {
    refusals.set('upload', notMutable('group_ai_running'))
    const w = await mountTab(v01Target())
    await pick(w, [file('a.txt')])
    expect(failureText(w)).toBe('문서를 수정할 수 없는 상태라 거절되었습니다. 목록을 다시 읽었습니다.')
    expect(listCall).toHaveBeenCalledTimes(2)
    expect(activityRead).toHaveBeenCalledTimes(2)
    expect(isDisabled(addButton(w))).toBe(true)
    expect(isDisabled(deleteButton(w, '요구사항.xlsx'))).toBe(true)
    expect(reasonText(w)).toBe('AI 실행 중이라 첨부를 바꿀 수 없습니다.')
    await pick(w, [file('b.txt')])
    expect(uploadCall).toHaveBeenCalledTimes(1)
    await itemEl(w, '요구사항.xlsx').find('[data-test="attachments-open"]').trigger('click')
    await flushPromises()
    expect(saved).toHaveLength(1)
  })

  it('409 group_disposed on delete: disabled with the disposed reason', async () => {
    refusals.set('delete', notMutable('group_disposed'))
    const w = await mountTab(v01Target())
    await removeByConfirm(w, '요구사항.xlsx')
    expect(itemEl(w, '요구사항.xlsx').find('[data-test="attachments-item-failure"]').text()).toBe('문서를 수정할 수 없는 상태라 거절되었습니다. 목록을 다시 읽었습니다.')
    expect(reasonText(w)).toBe('폐기된 그룹이라 첨부를 바꿀 수 없습니다.')
    expect(isDisabled(deleteButton(w, '요구사항 (1).xlsx'))).toBe(true)
    expect(names(w)).toEqual(['요구사항.xlsx', '요구사항 (1).xlsx'])
  })

  it('409 DOCUMENT_NOT_MUTABLE without a known reason: disabled with the not-mutable reason', async () => {
    refusals.set('upload', notMutable())
    const w = await mountTab(v01Target())
    await pick(w, [file('a.txt')])
    expect(reasonText(w)).toBe('서버가 이 문서의 첨부 변경을 거절했습니다(수정 불가 상태). AI 실행이 끝나거나 Node를 다시 고르면 다시 확인합니다.')
    expect(isDisabled(addButton(w))).toBe(true)
  })

  it('423 GROUP_AI_RUN_LOCKED (the AI run started after the guard read): AI running reason; ai_invoke_finished opens it again', async () => {
    vi.useFakeTimers()
    const w = await mountTab(v01Target())
    stream.open()
    running = true
    // The guard read after the refusal sees the run as well.
    await pick(w, [file('a.txt')])
    await flushPromises()
    expect(failureText(w)).toBe('AI 실행 중이라 거절되었습니다. 목록을 다시 읽었습니다.')
    expect(reasonText(w)).toBe('AI 실행 중이라 첨부를 바꿀 수 없습니다.')
    running = false
    stream.emit('ai_invoke_finished', { groupId: V01_FLOW, docId: OTHER_DOC })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(w.find('[data-test="attachments-reason"]').exists()).toBe(false)
    expect(isDisabled(addButton(w))).toBe(false)
    await pick(w, [file('a.txt')])
    expect(uploadCall).toHaveBeenCalledTimes(2)
    expect(names(w)).toContain('a.txt')
  })

  it('a refusal the guard read does not explain stays until a guard event of the Flow arrives', async () => {
    vi.useFakeTimers()
    refusals.set('delete', notMutable('group_ai_running'))
    const w = await mountTab(v01Target())
    stream.open()
    await removeByConfirm(w, '요구사항.xlsx')
    // The guard read again says no run: the refusal still holds.
    expect(isDisabled(deleteButton(w, '요구사항.xlsx'))).toBe(true)
    // An event of another Flow does not clear it.
    stream.emit('ai_invoke_finished', { groupId: 'flowgate.v02.0049', docId: 'flowgate.v02.0049.0001-T' })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    expect(isDisabled(deleteButton(w, '요구사항.xlsx'))).toBe(true)
    stream.emit('document_explorer_refresh', { groupId: V01_FLOW, docId: null })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(isDisabled(deleteButton(w, '요구사항.xlsx'))).toBe(false)
  })

  it('an AI run from the start: adding and deleting are disabled with the reason; listing and opening work', async () => {
    running = true
    const w = await mountTab(v01Target())
    expect(names(w)).toHaveLength(2)
    expect(isDisabled(addButton(w))).toBe(true)
    expect(isDisabled(deleteButton(w, '요구사항.xlsx'))).toBe(true)
    expect(reasonText(w)).toBe('AI 실행 중이라 첨부를 바꿀 수 없습니다.')
    await itemEl(w, '요구사항.xlsx').find('[data-test="attachments-open"]').trigger('click')
    await flushPromises()
    expect(saved).toHaveLength(1)
  })

  it('a disposed group: adding and deleting are disabled with the disposed reason', async () => {
    disposed = true
    const w = await mountTab(v01Target())
    expect(reasonText(w)).toBe('폐기된 그룹이라 첨부를 바꿀 수 없습니다.')
    expect(isDisabled(deleteButton(w, '요구사항.xlsx'))).toBe(true)
  })

  it('a failed guard read does not block (the server refuses on the same facts)', async () => {
    activityRead.mockRejectedValue(networkError())
    const w = await mountTab(v01Target())
    expect(isDisabled(addButton(w))).toBe(false)
  })

  it('ai_invoke_started of the target Flow reads the guard again and closes changes', async () => {
    vi.useFakeTimers()
    const w = await mountTab(v01Target())
    stream.open()
    running = true
    stream.emit('ai_invoke_started', { groupId: V01_FLOW, docId: OTHER_DOC })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(isDisabled(addButton(w))).toBe(true)
    expect(listCall).toHaveBeenCalledTimes(1)
  })

  it('a reconnect reads the list again', async () => {
    vi.useFakeTimers()
    await mountTab(v01Target())
    stream.open()
    stream.reconnect()
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    expect(listCall).toHaveBeenCalledTimes(2)
  })
})

// --- Read-only Flows ---------------------------------------------------------------------------------

describe('attachments tab — read-only Flows (flowgate.v02.0048 T#8)', () => {
  it.each([
    ['conflict', { readOnly: 'conflict' as const, ownership: 'conflict' }, 'Flow 소유가 충돌해 읽기 전용입니다.'],
    ['revision', { readOnly: 'revision' as const, ownership: 'graph', viewMode: 'revision' }, '확정된 Graph revision 보기라 읽기 전용입니다. draft 보기에서 첨부를 바꿉니다.'],
  ])('%s: the list and opening work, adding and deleting are disabled with the reason and send nothing', async (_label, fields, text) => {
    const w = await mountTab(v01Target(fields))
    expect(names(w)).toHaveLength(2)
    expect(reasonText(w)).toBe(text)
    expect(isDisabled(addButton(w))).toBe(true)
    expect(isDisabled(deleteButton(w, '요구사항.xlsx'))).toBe(true)
    await pick(w, [file('a.txt')])
    await deleteButton(w, '요구사항.xlsx').trigger('click')
    expect(itemEl(w, '요구사항.xlsx').find('[data-test="attachments-confirm"]').exists()).toBe(false)
    expect(uploadCall).not.toHaveBeenCalled()
    expect(deleteCall).not.toHaveBeenCalled()
    await itemEl(w, '요구사항.xlsx').find('[data-test="attachments-open"]').trigger('click')
    await flushPromises()
    expect(saved).toHaveLength(1)
  })

  it('the graph draft view of the same Node: changes are enabled', async () => {
    const w = await mountTab(v01Target({ ownership: 'graph', viewMode: 'draft', readOnly: null }))
    expect(isDisabled(addButton(w))).toBe(false)
  })
})

// --- Node change -----------------------------------------------------------------------------------

describe('attachments tab — Node change (flowgate.v02.0048 T#8)', () => {
  it('an instruction Node and its report Node each show their own attachments', async () => {
    rows.set('flowgate.v02.0048.0012-T', [row('flowgate.v02.0048.0012-T', 'instruction.md', 10)])
    const w = await mountTab(v01Target({ docId: 'flowgate.v02.0048.0012-T', docType: 'T' }))
    expect(listCall).toHaveBeenLastCalledWith('flowgate.v02.0048.0012-T')
    expect(names(w)).toEqual(['instruction.md'])
    await w.setProps({ target: otherTarget() })
    await flushPromises()
    expect(listCall).toHaveBeenLastCalledWith(OTHER_DOC)
    expect(names(w)).toEqual(['report.pdf'])
    await w.setProps({ target: v01Target({ docId: 'flowgate.v02.0048.0012-T', docType: 'T' }) })
    await flushPromises()
    expect(names(w)).toEqual(['instruction.md'])
  })

  it('the previous Node’s slow read never replaces the new one', async () => {
    let releaseFirst: () => void = () => {}
    listCall.mockImplementationOnce((docId: string) => new Promise((resolve) => (releaseFirst = () => resolve(toAttachmentsList(listOf(docId))))))
    const w = await mountTab(v01Target())
    await w.setProps({ target: otherTarget() })
    await flushPromises()
    expect(names(w)).toEqual(['report.pdf'])
    releaseFirst()
    await flushPromises()
    expect(names(w)).toEqual(['report.pdf'])
  })

  it('a late refusal of the previous Node is not shown on the new one, and what it blocked does not carry over', async () => {
    let refuse: () => void = () => {}
    uploadCall.mockImplementationOnce(() => new Promise((_resolve, reject) => (refuse = () => reject(notMutable('group_ai_running')))))
    const w = await mountTab(v01Target())
    await pick(w, [file('a.txt')])
    await w.setProps({ target: otherTarget() })
    await flushPromises()
    refuse()
    await flushPromises()
    expect(w.find('[data-test="attachments-failure"]').exists()).toBe(false)
    expect(isDisabled(addButton(w))).toBe(false)
    expect(names(w)).toEqual(['report.pdf'])
  })

  it('a pending delete confirm belongs to its document', async () => {
    const w = await mountTab(v01Target())
    await deleteButton(w, '요구사항.xlsx').trigger('click')
    await w.setProps({ target: otherTarget() })
    await flushPromises()
    await w.setProps({ target: v01Target() })
    await flushPromises()
    expect(w.find('[data-test="attachments-confirm"]').exists()).toBe(false)
  })
})

// --- In the Frame ----------------------------------------------------------------------------------

describe('attachments tab in the Frame (real registry)', () => {
  async function mountFrame(selection: FlowGraphSelection) {
    wrapper = mount(DesignerInspector, { props: { selection }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
    await flushPromises()
    return wrapper
  }

  const tabLabels = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').map((tab) => tab.text())
  const openTab = async (w: VueWrapper<any>, label: string) => {
    await w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').find((tab) => tab.text().startsWith(label))!.trigger('click')
    await flushPromises()
  }

  it('the attachments tab carries the number of attachments of the selected Node’s document', async () => {
    const w = await mountFrame(SELECTIONS.v01)
    expect(tabLabels(w)).toContain('첨부 (2)')
    await openTab(w, '첨부')
    expect(w.find('[data-test="inspector-attachments"]').exists()).toBe(true)
    expect(names(w)).toEqual(['요구사항.xlsx', '요구사항 (1).xlsx'])
  })

  it('no attachment: the tab shows no number', async () => {
    rows.set(V01_DOC, [])
    const w = await mountFrame(SELECTIONS.v01)
    expect(tabLabels(w)).toContain('첨부')
    expect(tabLabels(w).some((label) => /^첨부 \(/.test(label))).toBe(false)
  })

  it('adding and deleting in the tab update the number (no server event: the folder’s own signal)', async () => {
    const w = await mountFrame(SELECTIONS.v01)
    await openTab(w, '첨부')
    await pick(w, [file('a.txt')])
    await flushPromises()
    expect(tabLabels(w)).toContain('첨부 (3)')
    await removeByConfirm(w, '요구사항.xlsx')
    await removeByConfirm(w, '요구사항 (1).xlsx')
    await flushPromises()
    expect(tabLabels(w)).toContain('첨부 (1)')
    await removeByConfirm(w, 'a.txt')
    await flushPromises()
    expect(tabLabels(w).some((label) => /^첨부 \(/.test(label))).toBe(false)
  })

  it('a refused upload leaves the number as it was', async () => {
    refusals.set('upload', notMutable('group_ai_running'))
    const w = await mountFrame(SELECTIONS.v01)
    await openTab(w, '첨부')
    await pick(w, [file('a.txt')])
    expect(tabLabels(w)).toContain('첨부 (2)')
  })

  it('opening a file another user deleted reads the list and the number again', async () => {
    const w = await mountFrame(SELECTIONS.v01)
    await openTab(w, '첨부')
    rows.set(V01_DOC, [rows.get(V01_DOC)![0]])
    await itemEl(w, '요구사항 (1).xlsx').find('[data-test="attachments-open"]').trigger('click')
    await flushPromises()
    expect(names(w)).toEqual(['요구사항.xlsx'])
    expect(tabLabels(w)).toContain('첨부 (1)')
  })

  it('a delete refused because the file is already gone reads the number again', async () => {
    const w = await mountFrame(SELECTIONS.v01)
    await openTab(w, '첨부')
    rows.set(V01_DOC, [rows.get(V01_DOC)![1]])
    await removeByConfirm(w, '요구사항.xlsx')
    await flushPromises()
    expect(tabLabels(w)).toContain('첨부 (1)')
  })

  it('the number follows the selected Node’s document', async () => {
    const w = await mountFrame(SELECTIONS.v01)
    const other = selectionOf(V01_GRAPH, V01_TASK_NODE.id)
    await w.setProps({ selection: { ...other, node: { ...other.node, id: `doc:${OTHER_DOC}`, document: { ...other.node.document!, docId: OTHER_DOC } } } })
    await flushPromises()
    expect(listCall).toHaveBeenCalledWith(OTHER_DOC)
    expect(tabLabels(w)).toContain('첨부 (1)')
  })

  it('a failed count read shows the tab without a number', async () => {
    listCall.mockRejectedValue(networkError())
    const w = await mountFrame(SELECTIONS.v01)
    expect(tabLabels(w)).toContain('첨부')
  })

  it('a Node without a document has no attachments tab and reads nothing', async () => {
    const w = await mountFrame(SELECTIONS.v01NoDocument)
    expect(tabLabels(w)).toEqual([])
    expect(listCall).not.toHaveBeenCalled()
  })

  it.each(['graph', 'unassigned', 'conflict'] as const)('the %s Flow shows the attachments tab too (a document feature)', async (name) => {
    const w = await mountFrame(SELECTIONS[name])
    expect(tabLabels(w).some((label) => label.startsWith('첨부'))).toBe(true)
  })

  it('no AI hand-off mark or toggle appears (D#1 §8.4)', async () => {
    const w = await mountFrame(SELECTIONS.v01)
    await openTab(w, '첨부')
    const text = w.text()
    for (const word of ['AI 전달', '🤖']) expect(text).not.toContain(word)
    expect(w.find('[data-test="inspector-attachments"] input[type="checkbox"]').exists()).toBe(false)
  })
})
