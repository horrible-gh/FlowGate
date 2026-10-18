/**
 * flowgate.v02.0068 T#1 — the Inspector's document lines with [업로드][다운로드] (0068 D §3.1, §6.1; T §3-4 / §3-5):
 * - the common slot list draws the group (its `io` slot) before the line's buttons, only on a line that stands
 *   for a document of its own — not on a line without a document ([작성]), a result line or a nav line — and a
 *   caller that gives no group keeps its lines as they were;
 * - on the real Frame, a card's document lines carry the group for the line's own document (not the card's
 *   bound document) and the presses go through the coordinator for that id;
 * - an edit request (✎) while an upload guards the document opens its window in the view;
 * - the display part: accessible names, the enabled hint, a disabled reason, the progress spinner.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import InspectorSlotList, { type InspectorSlotView } from '@/features/designer/inspector/parts/InspectorSlotList.vue'
import { closeAllDocumentWindows, documentWindowList } from '@/features/documentWindow'
import DocumentIoButtons from '@/features/documentWindow/DocumentIoButtons.vue'
import { documentIoGuarded, resetDocumentEditRegistry } from '@/features/documentWindow/documentEditRegistry'
import { documentIoUpload, resetDocumentIo } from '@/features/documentWindow/documentIo'
import { prepareInspectorI18n, selectionOf } from './support/inspectorSupport'

const settingsNodeRead = vi.hoisted(() => vi.fn())
const infoRead = vi.hoisted(() => vi.fn())
const download = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const offline = () => Promise.reject(Object.assign(new Error('offline'), { request: {} }))
  return { ...actual, getRequest: vi.fn(offline), postRequest: vi.fn(offline), putRequest: vi.fn(offline), patchRequest: vi.fn(offline) }
})

vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    settingsNodeRead,
    documentInfoRead: (...args: unknown[]) => infoRead(...args),
    documentDownload: (...args: unknown[]) => download(...args),
  }
})

const i18n = prepareInspectorI18n()

const FLOW = 'flowgate.v02.0068'
const doc = (key: string, type: string) => `${FLOW}.${key}-${type}`

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  settingsNodeRead.mockReset().mockResolvedValue({ cardKind: 'run', locked: false, fields: { instruction: { value: 'use', stored: null, default: 'use', choices: ['use', 'omit'] } } })
  infoRead.mockReset().mockImplementation(async (docId: string) => ({ docId, title: '', isEditable: true, downloadAvailable: true }))
  download.mockReset()
  closeAllDocumentWindows()
  resetDocumentEditRegistry()
  resetDocumentIo()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  closeAllDocumentWindows()
  resetDocumentEditRegistry()
  resetDocumentIo()
  document.body.innerHTML = ''
})

// --- The common slot list ---------------------------------------------------------------------------------

const Probe = defineComponent({
  props: { docId: { type: String, required: true } },
  setup: (props) => () => h('span', { 'data-test': 'io-probe', 'data-doc': props.docId }),
})

const LINES: InspectorSlotView[] = [
  { key: 'instruction', name: '지시서', id: 'd.1', open: 'enabled' },
  { key: 'blank', name: '레포트', id: 'd.2', open: 'enabled', blank: true },
  { key: 'missing', name: '빈 줄', id: null, empty: true, open: 'none' },
  { key: 'result', kind: 'result', name: '확인 결과', id: 'd.3', open: 'enabled' },
  { key: 'nav', name: '전개', id: 'd.4', open: 'enabled', nav: { label: 'Git' } },
]

function mountList(withIo: boolean) {
  return mount(InspectorSlotList, {
    props: { slots: LINES, editable: true },
    slots: withIo ? { io: ({ docId }: { docId: string }) => h(Probe, { docId }) } : {},
    global: { plugins: [i18n] },
  })
}

describe('the slot list’s io slot (parts)', () => {
  it('only lines with a document of their own: a document line, a blank one — not empty, result or nav lines', () => {
    const w = mountList(true)
    const rows = w.findAll('[data-test="inspector-slot"]')
    const probe = (row: (typeof rows)[number]) => row.find('[data-test="io-probe"]')
    expect(rows.map((row) => (probe(row).exists() ? probe(row).attributes('data-doc') : null))).toEqual(['d.1', 'd.2', null, null, null])
  })

  it('the group sits in its own column right before the line’s buttons (history · open · ✎ keep their order)', () => {
    const w = mountList(true)
    const row = w.findAll('[data-test="inspector-slot"]')[0]
    const children = [...row.element.children].map((node) => node.getAttribute('data-test') ?? node.className)
    expect(children.slice(-2)).toEqual(['inspector-slot-io', 'inspector-slot-buttons'])
    expect(row.classes()).toContain('has-io')
    expect(row.get('[data-test="inspector-slot-buttons"]').findAll('button').map((button) => button.attributes('data-test'))).toEqual([
      'inspector-slot-history',
      'inspector-slot-open',
      'inspector-slot-edit',
    ])
  })

  it('a caller without the io slot keeps its lines as they were', () => {
    const w = mountList(false)
    expect(w.find('[data-test="inspector-slot-io"]').exists()).toBe(false)
    expect(w.findAll('.has-io')).toHaveLength(0)
  })
})

// --- The real Frame -----------------------------------------------------------------------------------------

function line(slot: string, docType: string, docId: string | null, author: 'human' | 'run'): FlowGraphCardDocument {
  return { slot, docType, author, docId, title: `카드 · ${slot}`, status: 'draft', docReviewStatus: null }
}

function taskNode(lines: FlowGraphCardDocument[]): FlowGraphNode {
  return {
    id: 'gn_t',
    kind: 'task',
    title: '작업 카드',
    state: 'draft',
    docType: 'T',
    x: 0,
    y: 0,
    structureEditable: true,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: '작업 카드', assignee: null, rootBound: false },
    // The card's bound document is the instruction; each line still acts on its own document.
    document: { docId: doc('0009', 'T'), typeCode: 'T', seq: null, title: '지시', docReviewStatus: null, status: 'draft', supersedes: [] },
    cardDocuments: lines,
  }
}

function graphOf(node: FlowGraphNode): FlowGraph {
  return { flowId: FLOW, nodes: [node], edges: [], ownership: 'unassigned', viewMode: 'draft', draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null } }
}

async function mountFrame(node: FlowGraphNode) {
  wrapper = mount(DesignerInspector, { attachTo: document.body, props: { selection: selectionOf(graphOf(node), node.id) }, global: { plugins: [i18n] } })
  await flushPromises()
  return wrapper
}

const rows = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-body"] [data-test="inspector-slot"]')

describe('card document lines on the Frame', () => {
  it('each document line has the group of its own document, before its buttons; a line without a document has none', async () => {
    const w = await mountFrame(taskNode([line('instruction', 'T', doc('0009', 'T'), 'human'), line('report', 'TR', null, 'run')]))
    const [instruction, report] = rows(w)
    expect(instruction.get('[data-test="document-io"]').attributes('data-doc')).toBe(doc('0009', 'T'))
    expect(instruction.findAll('button').map((button) => button.attributes('data-test'))).toEqual([
      'inspector-slot-name',
      'document-io-upload',
      'document-io-download',
      // the line's own buttons as before: [AI 호출] · history · open · ✎
      'inspector-slot-action',
      'inspector-slot-history',
      'inspector-slot-open',
      'inspector-slot-edit',
    ])
    expect(report.attributes('data-empty')).toBe('true')
    expect(report.find('[data-test="document-io"]').exists()).toBe(false)
  })

  it('the report line acts on the report, not on the card’s bound document', async () => {
    const report = doc('0010', 'TR')
    download.mockResolvedValue({ blob: new Blob(['# r']), disposition: null })
    const w = await mountFrame(taskNode([line('instruction', 'T', doc('0009', 'T'), 'human'), line('report', 'TR', report, 'run')]))
    const row = rows(w)[1]
    expect(row.get('[data-test="document-io"]').attributes('data-doc')).toBe(report)
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await row.get('[data-test="document-io-download"]').trigger('click')
    await flushPromises()
    expect(download).toHaveBeenCalledWith(report)
    anchorClick.mockRestore()
  })

  it('the requirement card’s AI 위임 line (a result line) has no group', async () => {
    const node: FlowGraphNode = {
      ...taskNode([line('requirement', 'R', doc('0001', 'R'), 'human'), line('delegation', 'M', doc('0002', 'M'), 'run')]),
      id: 'gn_r',
      docType: 'R',
      definition: { nodeType: 'document', docType: 'R', paletteItemId: null, label: '요구사항', assignee: null, rootBound: false },
      document: { docId: doc('0001', 'R'), typeCode: 'R', seq: null, title: 'R', docReviewStatus: null, status: 'draft', supersedes: [] },
    }
    const w = await mountFrame(node)
    const [requirement, delegation] = rows(w)
    expect(requirement.find('[data-test="document-io"]').exists()).toBe(true)
    expect(delegation.attributes('data-kind')).toBe('result')
    expect(delegation.find('[data-test="document-io"]').exists()).toBe(false)
  })

  it('✎ while an upload guards the document opens the window in the view; after the release, the editor', async () => {
    const id = doc('0009', 'T')
    const w = await mountFrame(taskNode([line('instruction', 'T', id, 'human')]))
    let finishRead: (text: string) => void = () => {}
    const run = documentIoUpload(id, { pick: async () => new File(['x'], 'x.md'), read: () => new Promise((resolve) => (finishRead = resolve)) })
    await flushPromises()
    expect(documentIoGuarded(id)).toBe(true)
    const row = rows(w)[0]
    expect(row.get('[data-test="document-io-upload"]').attributes('title')).toBe('업로드 처리 중')
    await row.get('[data-test="inspector-slot-edit"]').trigger('click')
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([[id, 'view']])
    finishRead('   ')
    await run
    closeAllDocumentWindows()
    await row.get('[data-test="inspector-slot-edit"]').trigger('click')
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([[id, 'edit']])
  })
})

// --- The display part ---------------------------------------------------------------------------------------

describe('DocumentIoButtons (display only)', () => {
  const view = (enabled: boolean, busy = false, reason: string | null = null) => ({ enabled, busy, reason }) as never

  it('names, enabled hints, presses', async () => {
    const w = mount(DocumentIoButtons, { props: { upload: view(true), download: view(true) }, global: { plugins: [i18n] } })
    const [up, down] = w.findAll('button')
    expect([up.attributes('aria-label'), down.attributes('aria-label')]).toEqual(['업로드', '다운로드'])
    expect([up.attributes('title'), down.attributes('title')]).toEqual(['파일 내용으로 문서 전체를 교체', '서버에 저장된 문서를 파일로 받기'])
    expect(w.find('[data-test="document-io-divider"]').attributes('role')).toBe('separator')
    await up.trigger('click')
    await down.trigger('click')
    expect(w.emitted('upload')).toHaveLength(1)
    expect(w.emitted('download')).toHaveLength(1)
  })

  it('a disabled button stays with its reason; a running one spins and takes no press', async () => {
    const w = mount(DocumentIoButtons, {
      props: { upload: view(false, true, 'uploading'), download: view(false, false, 'no_file') },
      global: { plugins: [i18n] },
    })
    const [up, down] = w.findAll('button')
    expect(up.attributes('disabled')).toBeDefined()
    expect(up.attributes('aria-busy')).toBe('true')
    expect(up.find('.document-io-spinner').exists()).toBe(true)
    expect(up.attributes('title')).toBe('업로드 처리 중')
    expect(down.attributes('title')).toBe('파일 없음')
    expect(down.attributes('disabled')).toBeDefined()
    await up.trigger('click')
    expect(w.emitted('upload')).toBeUndefined()
  })

  it('follows the locale', () => {
    i18n.global.locale.value = 'en'
    const w = mount(DocumentIoButtons, { props: { upload: view(false, false, 'forbidden'), download: view(true) }, global: { plugins: [i18n] } })
    const [up, down] = w.findAll('button')
    expect(up.attributes('title')).toBe('No permission')
    expect(down.attributes('aria-label')).toBe('Download')
  })
})
