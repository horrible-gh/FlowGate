/**
 * flowgate.v02.0053 T#6 — the attachments tab's [+] and drag and drop (0053 CH §3), with
 * `@/features/document` mocked as a small server:
 *
 * - [+] is one icon button (no '＋ 추가' text, no '파일당 최대 nMB' line); its tooltip carries the size limit;
 * - a document without attachments shows only '첨부 없음' (v7) besides [+];
 * - files dragged over the tab highlight it (only a drag that carries files, only while adding is possible,
 *   and it stays through the children the pointer crosses); dropping them uploads them through the same
 *   upload API, client limits and refusals as picked files, then reads the list again;
 * - a drop is ignored while adding is closed (read only, an AI run, a change on its way);
 * - in the real Frame, a card just placed (T#1 documents) uploads with [+] and with a drop;
 * - in the real Frame the whole tab Content takes the drop: files dragged onto the free space under an empty
 *   tab (the body itself, outside the tab's own box) highlight the tab, are not opened by the browser and
 *   upload the same way; the tab's box fills the body; another tab takes no drop.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import AttachmentsTab from '@/features/designer/inspector/features/attachments/AttachmentsTab.vue'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { SELECTIONS, V01_TASK_NODE, prepareInspectorI18n, selectionOf, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream } from '../support/projectEventStream'

const listCall = vi.fn()
const uploadCall = vi.fn()
const activityRead = vi.fn()
const documentRead = vi.fn()
const detailRead = vi.fn()
const relationsRead = vi.fn()
const qaReadCall = vi.fn()
const settingsNodeRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    attachmentsRead: (...args: unknown[]) => listCall(...args),
    attachmentsUpload: (...args: unknown[]) => uploadCall(...args),
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    reviewDocumentRead: (...args: unknown[]) => documentRead(...args),
    // The other parts of the Frame in the placed-card case.
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
    qaRead: (...args: unknown[]) => qaReadCall(...args),
    settingsNodeRead: (...args: unknown[]) => settingsNodeRead(...args),
  }
})

// Anything else the Frame reads answers nothing.
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const offline = () => Promise.reject(Object.assign(new Error('offline'), { request: {} }))
  return { ...actual, getRequest: vi.fn(offline), postRequest: vi.fn(offline), putRequest: vi.fn(offline), patchRequest: vi.fn(offline) }
})

const { toAttachmentsList, toAttachmentsUploadResult, toReviewActivity, toReviewDocument, toDocumentDetail, toQaThread } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const V01_DOC = V01_TASK_NODE.document!.docId

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
const refusals = new Map<string, unknown>()

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
const envelope = (status: number, code: string, details: Record<string, unknown> = {}) => axiosError(status, { error: { code, message: 'm', details } })

const row = (docId: string, filename: string, size: number): ServerRow => ({
  doc_id: docId,
  original_filename: filename,
  filename,
  size,
  content_type: 'application/octet-stream',
  uploaded_by: 'u1',
  uploaded_at: '2026-10-11T10:00:00Z',
})

const listOf = (docId: string) => ({ data: { doc_id: docId, attachments: [...(rows.get(docId) ?? [])], count: rows.get(docId)?.length ?? 0 } })
const docRow = (docId: string) => ({ doc_id: docId, group_id: docId.split('.').slice(0, 3).join('.'), type_code: 'T', title: docId, status: 'open', doc_review_status: null, group_disposed: false })

function serve() {
  listCall.mockImplementation(async (docId: string) => toAttachmentsList(listOf(docId)))
  uploadCall.mockImplementation(async (docId: string, files: File[]) => {
    const refusal = refusals.get('upload')
    refusals.delete('upload')
    if (refusal) throw refusal
    const added = files.map((file) => row(docId, file.name, file.size))
    rows.set(docId, [...(rows.get(docId) ?? []), ...added])
    return toAttachmentsUploadResult({ data: { doc_id: docId, attachments: added, count: added.length } })
  })
  activityRead.mockImplementation(async (groupId: string) =>
    toReviewActivity(running ? { active: true, run_id: 'run_1', status: 'running', group_id: groupId } : { active: false, group_id: groupId }),
  )
  documentRead.mockImplementation(async (docId: string) => toReviewDocument(docRow(docId)))
  detailRead.mockImplementation(async (docId: string) => toDocumentDetail(docRow(docId)))
  relationsRead.mockImplementation(async () => [])
  qaReadCall.mockImplementation(async (docId: string) => toQaThread({ ok: true, qa: { doc_id: docId, status: null, items: [] } }))
  settingsNodeRead.mockResolvedValue({ cardKind: 'run', locked: false, fields: { instruction: { value: 'use', stored: null, default: 'use', choices: ['use', 'omit'] } } })
}

// --- Mounting --------------------------------------------------------------------------------------

let pinia: Pinia
let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  installProjectEventStream()
  rows.clear()
  refusals.clear()
  running = false
  for (const mock of [listCall, uploadCall, activityRead, documentRead, detailRead, relationsRead, qaReadCall, settingsNodeRead]) mock.mockReset()
  rows.set(V01_DOC, [row(V01_DOC, 'spec.md', 2048)])
  serve()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

async function mountTab(target: InspectorTarget) {
  wrapper = mount(AttachmentsTab, { props: { target }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

const v01Target = (fields: Partial<InspectorTarget> = {}): InspectorTarget => ({ ...targetOf(SELECTIONS.v01), ...fields })

const area = (w: VueWrapper<any>) => w.get('[data-test="inspector-attachments"]')
const addButton = (w: VueWrapper<any>) => w.get('[data-test="attachments-add"]')
const names = (w: VueWrapper<any>) => w.findAll('[data-test="attachments-item"]').map((el) => el.attributes('data-name'))
const isDisabled = (el: ReturnType<VueWrapper<any>['get']>) => el.attributes('disabled') !== undefined

const file = (name: string, size = 4) => new File(['x'.repeat(Math.min(size, 16))], name, { type: 'text/plain' })
const bigFile = (name: string) => {
  const f = file(name)
  Object.defineProperty(f, 'size', { value: 20 * 1024 * 1024 + 1 })
  return f
}

/** A drag from the desktop carries `Files`; a drag of page text carries `text/plain`. */
const filesDrag = (files: File[] = []) => ({ dataTransfer: { types: ['Files'], files, dropEffect: 'none' } })
const textDrag = () => ({ dataTransfer: { types: ['text/plain'], files: [], dropEffect: 'none' } })

/** A drag event dispatched by hand, to read `defaultPrevented` and the drop effect it leaves. */
function dragEvent(type: string, files: File[] = []) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  const transfer = { types: ['Files'], files, dropEffect: 'none' }
  Object.defineProperty(event, 'dataTransfer', { value: transfer })
  return { event, transfer }
}

async function drop(target: ReturnType<VueWrapper<any>['get']>, files: File[]) {
  await target.trigger('dragenter', filesDrag())
  await target.trigger('dragover', filesDrag())
  await target.trigger('drop', filesDrag(files))
  await flushPromises()
}

// --- [+] -------------------------------------------------------------------------------------------

describe('[+] alone (flowgate.v02.0053 T#6)', () => {
  it('is one icon button: no ＋ 추가 text and no size line; the size limit is its tooltip', async () => {
    const w = await mountTab(v01Target())
    const plus = addButton(w)
    expect(plus.element.tagName).toBe('BUTTON')
    expect([plus.attributes('data-icon'), plus.attributes('aria-label'), plus.attributes('title'), plus.text()]).toEqual([
      'add',
      '파일 추가',
      '파일 추가 · 파일당 최대 20MB',
      '',
    ])
    expect(plus.find('svg').exists()).toBe(true)
    const text = area(w).text()
    for (const word of ['＋ 추가', '추가', '파일당 최대', 'MB 까지']) expect(text).not.toContain(word)
  })

  it('a document without attachments shows only 첨부 없음 besides [+]', async () => {
    rows.set(V01_DOC, [])
    const w = await mountTab(v01Target())
    expect(area(w).text()).toBe('첨부 없음')
    expect(w.findAll('[data-test="inspector-pane-state"]')).toHaveLength(1)
    expect(isDisabled(addButton(w))).toBe(false)
  })

  it('en / ja: the same icon with the tooltip in the locale', async () => {
    i18n.global.locale.value = 'en'
    const w = await mountTab(v01Target())
    expect(addButton(w).attributes('title')).toBe('Add files · up to 20MB per file')
    i18n.global.locale.value = 'ja'
    await flushPromises()
    expect(addButton(w).attributes('title')).toBe('ファイルを追加 · ファイルあたり最大 20MB')
  })

  it('[+] opens the file picker; picked files are uploaded', async () => {
    const w = await mountTab(v01Target())
    const clicked = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    await addButton(w).trigger('click')
    expect(clicked).toHaveBeenCalledTimes(1)
    const input = w.get('[data-test="attachments-picker"]')
    Object.defineProperty(input.element, 'files', { value: [file('a.txt')], configurable: true })
    await input.trigger('change')
    await flushPromises()
    expect(uploadCall).toHaveBeenCalledTimes(1)
    expect(names(w)).toEqual(['spec.md', 'a.txt'])
  })

  it('a closed [+] (read only) neither opens the picker nor sends', async () => {
    const w = await mountTab(v01Target({ readOnly: 'conflict' }))
    const clicked = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    expect(isDisabled(addButton(w))).toBe(true)
    await addButton(w).trigger('click')
    expect(clicked).not.toHaveBeenCalled()
  })
})

// --- Drag and drop ---------------------------------------------------------------------------------

describe('drag and drop on the attachments tab (flowgate.v02.0053 T#6)', () => {
  it('files dragged over the tab highlight it; leaving the tab ends it', async () => {
    const w = await mountTab(v01Target())
    expect(area(w).attributes('data-drop')).toBe('idle')
    await area(w).trigger('dragenter', filesDrag())
    expect(area(w).attributes('data-drop')).toBe('active')
    expect(area(w).classes()).toContain('is-dropping')
    await area(w).trigger('dragleave', filesDrag())
    expect(area(w).attributes('data-drop')).toBe('idle')
    expect(area(w).classes()).not.toContain('is-dropping')
  })

  it('the highlight stays while the pointer crosses the rows inside the tab', async () => {
    const w = await mountTab(v01Target())
    const rowEl = w.get('[data-test="attachments-item"]')
    await area(w).trigger('dragenter', filesDrag())
    // Entering a child fires dragenter there (bubbling) before dragleave of the parent.
    await rowEl.trigger('dragenter', filesDrag())
    await area(w).trigger('dragleave', filesDrag())
    expect(area(w).attributes('data-drop')).toBe('active')
    await rowEl.trigger('dragleave', filesDrag())
    expect(area(w).attributes('data-drop')).toBe('idle')
  })

  it('dragover accepts the files (default prevented, copy effect) so the browser does not open them', async () => {
    const w = await mountTab(v01Target())
    const event = new Event('dragover', { bubbles: true, cancelable: true })
    const transfer = { types: ['Files'], files: [], dropEffect: 'none' }
    Object.defineProperty(event, 'dataTransfer', { value: transfer })
    area(w).element.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    expect(transfer.dropEffect).toBe('copy')
  })

  it('dropping files uploads them to the target document, then the list is read again', async () => {
    const w = await mountTab(v01Target())
    await drop(area(w), [file('a.txt'), file('b.png')])
    expect(uploadCall).toHaveBeenCalledTimes(1)
    const [docId, files] = uploadCall.mock.calls[0]
    expect(docId).toBe(V01_DOC)
    expect((files as File[]).map((f) => f.name)).toEqual(['a.txt', 'b.png'])
    expect(listCall).toHaveBeenCalledTimes(2)
    expect(names(w)).toEqual(['spec.md', 'a.txt', 'b.png'])
    expect(area(w).attributes('data-drop')).toBe('idle')
  })

  it('a drop on a row inside the tab uploads as well', async () => {
    const w = await mountTab(v01Target())
    await drop(w.get('[data-test="attachments-item"]'), [file('c.txt')])
    expect(uploadCall).toHaveBeenCalledTimes(1)
    expect(names(w)).toEqual(['spec.md', 'c.txt'])
  })

  it('a drop on an empty document uploads and 첨부 없음 gives way to the row', async () => {
    rows.set(V01_DOC, [])
    const w = await mountTab(v01Target())
    await drop(area(w), [file('first.txt')])
    expect(names(w)).toEqual(['first.txt'])
    expect(area(w).text()).not.toContain('첨부 없음')
  })

  it('a drag of page text (no files) is not taken: no highlight, nothing sent', async () => {
    const w = await mountTab(v01Target())
    await area(w).trigger('dragenter', textDrag())
    expect(area(w).attributes('data-drop')).toBe('idle')
    await area(w).trigger('drop', textDrag())
    await flushPromises()
    expect(uploadCall).not.toHaveBeenCalled()
  })

  it.each([
    ['a file over 20 MiB', () => [file('ok.txt'), bigFile('big.bin')], 'big.bin 파일이 20MB를 넘어 올리지 않았습니다.'],
    ['21 files at once', () => Array.from({ length: 21 }, (_, i) => file(`f${i}.txt`)), '한 번에 20개까지 올릴 수 있습니다.'],
  ])('dropped files keep the client limits: %s is not sent and says why', async (_label, files, text) => {
    const w = await mountTab(v01Target())
    await drop(area(w), files())
    expect(uploadCall).not.toHaveBeenCalled()
    expect(w.get('[data-test="attachments-failure"]').text()).toBe(text)
  })

  it('a dropped upload refused by the server shows why, like a picked one', async () => {
    refusals.set('upload', envelope(413, 'ATTACHMENT_TOO_LARGE'))
    const w = await mountTab(v01Target())
    await drop(area(w), [file('a.txt')])
    expect(w.get('[data-test="attachments-failure"]').text()).toBe('파일이 너무 커서 거절되었습니다.')
    expect(names(w)).toEqual(['spec.md'])
  })

  it.each([
    ['ownership conflict', () => ({ readOnly: 'conflict' as const })],
    ['revision view', () => ({ readOnly: 'revision' as const })],
  ])('%s: no highlight and a drop sends nothing', async (_label, fields) => {
    const w = await mountTab(v01Target(fields()))
    await area(w).trigger('dragenter', filesDrag())
    expect(area(w).attributes('data-drop')).toBe('idle')
    await drop(area(w), [file('a.txt')])
    expect(uploadCall).not.toHaveBeenCalled()
  })

  it('an AI run of the Flow: no highlight, a drop sends nothing; dragover refuses the drop', async () => {
    running = true
    const w = await mountTab(v01Target())
    const event = new Event('dragover', { bubbles: true, cancelable: true })
    const transfer = { types: ['Files'], files: [], dropEffect: 'copy' }
    Object.defineProperty(event, 'dataTransfer', { value: transfer })
    area(w).element.dispatchEvent(event)
    expect(transfer.dropEffect).toBe('none')
    await drop(area(w), [file('a.txt')])
    expect(uploadCall).not.toHaveBeenCalled()
    expect(area(w).attributes('data-drop')).toBe('idle')
  })

  it('a second drop while the first upload waits sends nothing', async () => {
    let release: () => void = () => {}
    uploadCall.mockImplementationOnce(
      (docId: string, files: File[]) =>
        new Promise((resolve) => {
          release = () => {
            const added = files.map((f) => row(docId, f.name, f.size))
            rows.set(docId, [...rows.get(docId)!, ...added])
            resolve(toAttachmentsUploadResult({ data: { doc_id: docId, attachments: added } }))
          }
        }),
    )
    const w = await mountTab(v01Target())
    await drop(area(w), [file('a.txt')])
    expect(addButton(w).attributes('data-busy')).toBe('true')
    await drop(area(w), [file('b.txt')])
    expect(uploadCall).toHaveBeenCalledTimes(1)
    release()
    await flushPromises()
    expect(names(w)).toEqual(['spec.md', 'a.txt'])
    expect(addButton(w).attributes('data-busy')).toBe('false')
  })

  it('a Node change ends a drag in progress', async () => {
    const w = await mountTab(v01Target())
    await area(w).trigger('dragenter', filesDrag())
    await w.setProps({ target: v01Target({ docId: 'flowgate.v02.0048.0013-TR', nodeId: 'doc:flowgate.v02.0048.0013-TR' }) })
    await flushPromises()
    expect(area(w).attributes('data-drop')).toBe('idle')
  })
})

// --- A card just placed, in the real Frame -----------------------------------------------------------

describe('a card just placed: [+] and a drop upload (flowgate.v02.0053 T#6 완료 기준)', () => {
  const FLOW = 'flowgate.v02.0060'
  const INSTRUCTION = `${FLOW}.0009-T`

  const line = (slot: string, docType: string, docId: string, author: 'human' | 'run'): FlowGraphCardDocument => ({
    slot,
    docType,
    author,
    docId,
    title: `카드 · ${slot}`,
    status: 'draft',
    docReviewStatus: null,
  })

  const placedTask = (): FlowGraphNode => ({
    id: 'gn_t',
    kind: 'task',
    title: '새 카드',
    state: 'draft',
    docType: 'T',
    x: 0,
    y: 0,
    structureEditable: true,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: '새 카드', assignee: null, rootBound: false },
    document: { docId: INSTRUCTION, typeCode: 'T', seq: null, title: '카드 · instruction', docReviewStatus: null, status: 'draft', supersedes: [] },
    cardDocuments: [line('instruction', 'T', INSTRUCTION, 'human'), line('report', 'TR', `${FLOW}.0010-TR`, 'run')],
  })

  const graphOf = (node: FlowGraphNode): FlowGraph => ({
    flowId: FLOW,
    nodes: [node],
    edges: [],
    ownership: 'unassigned',
    viewMode: 'draft',
    draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
  })

  it('the 첨부 tab of the new card shows 첨부 없음 and [+]; picked and dropped files upload to its document', async () => {
    const node = placedTask()
    wrapper = mount(DesignerInspector, {
      props: { selection: selectionOf(graphOf(node), node.id) },
      global: { plugins: [i18n, pinia] },
      attachTo: document.body,
    })
    await flushPromises()
    const tabs = () => wrapper!.findAll('[data-test="designer-inspector-tabs"] [role="tab"]')
    await tabs().find((tab) => tab.text().startsWith('첨부'))!.trigger('click')
    await flushPromises()
    const w = wrapper
    expect(area(w).text()).toBe('첨부 없음')
    expect(addButton(w).attributes('title')).toBe('파일 추가 · 파일당 최대 20MB')

    const input = w.get('[data-test="attachments-picker"]')
    Object.defineProperty(input.element, 'files', { value: [file('picked.txt')], configurable: true })
    await input.trigger('change')
    await flushPromises()
    await drop(area(w), [file('dropped.png')])

    expect(uploadCall.mock.calls.map(([docId, files]) => [docId, (files as File[]).map((f) => f.name)])).toEqual([
      [INSTRUCTION, ['picked.txt']],
      [INSTRUCTION, ['dropped.png']],
    ])
    expect(names(w)).toEqual(['picked.txt', 'dropped.png'])
    expect(tabs().map((tab) => tab.text())).toContain('첨부 (2)')
  })

  async function mountOnAttachments() {
    const node = placedTask()
    wrapper = mount(DesignerInspector, {
      props: { selection: selectionOf(graphOf(node), node.id) },
      global: { plugins: [i18n, pinia] },
      attachTo: document.body,
    })
    await flushPromises()
    await selectTab('첨부')
    return wrapper
  }

  async function selectTab(label: string) {
    await wrapper!.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').find((tab) => tab.text().startsWith(label))!.trigger('click')
    await flushPromises()
  }

  const bodyOf = (w: VueWrapper<any>) => w.get('[data-test="designer-inspector-body"]')

  it('the tab fills the body: its section takes the free height (is-fill) with the drop area inside', async () => {
    const w = await mountOnAttachments()
    const section = w.get('[data-test="designer-inspector-section"][data-key="attachments"]')
    expect(section.attributes('data-fill')).toBe('true')
    expect(section.classes()).toContain('is-fill')
    expect(section.element.contains(area(w).element)).toBe(true)
  })

  it('files dragged onto the free space under an empty tab (the body itself) highlight the tab, are not opened and upload', async () => {
    const w = await mountOnAttachments()
    const body = bodyOf(w)
    expect(area(w).text()).toBe('첨부 없음')
    // The events land on the body, outside the tab's own box.
    expect(area(w).element.contains(body.element)).toBe(false)

    const enter = dragEvent('dragenter')
    body.element.dispatchEvent(enter.event)
    await flushPromises()
    expect(enter.event.defaultPrevented).toBe(true)
    expect(area(w).attributes('data-drop')).toBe('active')
    expect(area(w).classes()).toContain('is-dropping')

    const over = dragEvent('dragover')
    body.element.dispatchEvent(over.event)
    expect(over.event.defaultPrevented).toBe(true)
    expect(over.transfer.dropEffect).toBe('copy')

    const dropped = dragEvent('drop', [file('below.txt'), file('below.png')])
    body.element.dispatchEvent(dropped.event)
    await flushPromises()
    expect(dropped.event.defaultPrevented).toBe(true)
    // The same upload as [+]: the card's document, the files in order, then the list read again.
    expect(uploadCall.mock.calls.map(([docId, files]) => [docId, (files as File[]).map((f) => f.name)])).toEqual([
      [INSTRUCTION, ['below.txt', 'below.png']],
    ])
    expect(names(w)).toEqual(['below.txt', 'below.png'])
    expect(area(w).attributes('data-drop')).toBe('idle')
  })

  it('the highlight stays from the free space into the tab and ends when the drag leaves the body', async () => {
    const w = await mountOnAttachments()
    const body = bodyOf(w)
    await body.trigger('dragenter', filesDrag())
    // Into the tab's box: dragenter there (bubbling to the body) before dragleave of the body.
    await area(w).trigger('dragenter', filesDrag())
    await body.trigger('dragleave', filesDrag())
    expect(area(w).attributes('data-drop')).toBe('active')
    // Back to the free space, then out of the body.
    await body.trigger('dragenter', filesDrag())
    await area(w).trigger('dragleave', filesDrag())
    expect(area(w).attributes('data-drop')).toBe('active')
    await body.trigger('dragleave', filesDrag())
    expect(area(w).attributes('data-drop')).toBe('idle')
  })

  it('an AI run of the Flow: the free space refuses the drop too (no highlight, drop effect none, nothing sent)', async () => {
    running = true
    const w = await mountOnAttachments()
    const body = bodyOf(w)
    await body.trigger('dragenter', filesDrag())
    expect(area(w).attributes('data-drop')).toBe('idle')
    const over = dragEvent('dragover')
    body.element.dispatchEvent(over.event)
    // Still prevented so the browser does not open the file; the drop effect says it is not taken.
    expect(over.event.defaultPrevented).toBe(true)
    expect(over.transfer.dropEffect).toBe('none')
    const dropped = dragEvent('drop', [file('a.txt')])
    body.element.dispatchEvent(dropped.event)
    await flushPromises()
    expect(uploadCall).not.toHaveBeenCalled()
  })

  it('another tab takes no drop: the listeners leave the body with the attachments tab', async () => {
    const w = await mountOnAttachments()
    await selectTab('Q&A')
    expect(w.find('[data-test="inspector-attachments"]').exists()).toBe(false)
    const over = dragEvent('dragover')
    bodyOf(w).element.dispatchEvent(over.event)
    expect(over.event.defaultPrevented).toBe(false)
    const dropped = dragEvent('drop', [file('a.txt')])
    bodyOf(w).element.dispatchEvent(dropped.event)
    await flushPromises()
    expect(dropped.event.defaultPrevented).toBe(false)
    expect(uploadCall).not.toHaveBeenCalled()
    // Back on 첨부, the body takes the drop again.
    await selectTab('첨부')
    const again = dragEvent('drop', [file('b.txt')])
    bodyOf(w).element.dispatchEvent(again.event)
    await flushPromises()
    expect(uploadCall).toHaveBeenCalledTimes(1)
  })
})
