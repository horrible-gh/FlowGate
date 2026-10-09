/**
 * flowgate.v02.0048 T#5 — the documents tab with `@/features/document` mocked (D#1 §12.1): the
 * v01 and graph fixtures show their real-shaped data (the server answers go through the real
 * normalizer), ↗ 열기 calls openDocument (new tab, noopener, doc_id only), failed reads (404 /
 * 403 / 409 / network) show their reason and a retry, a failed superseded document stays listed,
 * a document event reads again, and a slow answer of the previous Node never covers the new one.
 * Last, the real registry puts the tab on document Nodes only, in every ownership.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import DocumentsTab from '@/features/designer/inspector/features/documents/DocumentsTab.vue'
import { DOCUMENTS_EVENTS } from '@/features/designer/inspector/features/documents/useDocuments'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { SELECTIONS, prepareInspectorI18n, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'

// The reads are mocked; normalization and error reading stay real.
const detailRead = vi.fn()
const relationsRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
  }
})

const { toDocumentDetail, toDocumentRecord } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

// --- Server answers (GET /api/v1/documents/{doc_id}, snake_case) -----------------------------------

const V01_DOC = 'flowgate.v02.0048.0011-TR'
const V01_PREVIOUS = 'flowgate.v02.0048.0009-TR'
const GRAPH_DOC = 'flowgate.v02.0049.0007-TS'

const V01_RESPONSE = {
  doc_id: V01_DOC,
  project_id: 'flowgate',
  group_id: 'flowgate.v02.0048',
  type_code: 'TR',
  seq: 11,
  title: '작업레포트 — T#1',
  status: 'open',
  doc_review_status: 'rejected',
  revision_no: 2,
  rejection_reason: '구조 검사 누락',
  rejection_history: [
    { rejection_id: 'rj_01', reason: '테스트 결과 누락', rejected_at: '2026-10-09T14:10:00+09:00', rejected_by: 'u1', ai_response: '테스트 결과를 추가했습니다.' },
    { rejection_id: 'rj_02', reason: '구조 검사 누락', rejected_at: '2026-10-09T15:00:00+09:00', rejected_by: 'u1', ai_response: null },
  ],
  ai_review: { id: 77 },
  ai_review_history: [{ id: 77 }],
  updated_at: '2026-10-09T15:00:00+09:00',
}

const V01_PREVIOUS_RESPONSE = {
  doc_id: V01_PREVIOUS,
  type_code: 'TR',
  seq: 9,
  title: '작업레포트 — T#1 (1차)',
  status: 'closed',
  doc_review_status: 'rejected',
  superseded_by: V01_DOC,
}

const GRAPH_RESPONSE = {
  doc_id: GRAPH_DOC,
  project_id: 'flowgate',
  group_id: 'flowgate.v02.0049',
  type_code: 'TS',
  seq: 7,
  title: '테스트 지시',
  status: 'closed',
  doc_review_status: 'approved',
  revision_no: 1,
  rejection_reason: null,
  rejection_history: [],
  ai_review: null,
  ai_review_history: [],
}

const RESPONSES: Record<string, unknown> = { [V01_DOC]: V01_RESPONSE, [V01_PREVIOUS]: V01_PREVIOUS_RESPONSE, [GRAPH_DOC]: GRAPH_RESPONSE }

const axiosError = (status: number, code?: string) => ({ isAxiosError: true, response: { status, data: code ? { detail: { code } } : { detail: 'x' } } })
const networkError = () => ({ isAxiosError: true, message: 'Network Error' })

/** The server as the mocked reads: answers by doc_id, or the failure set for it. */
const failures = new Map<string, unknown>()

function serveDocuments() {
  detailRead.mockImplementation(async (docId: string) => {
    if (failures.has(docId)) throw failures.get(docId)
    return toDocumentDetail(RESPONSES[docId])
  })
  relationsRead.mockImplementation(async (ids: readonly string[]) =>
    ids.map((docId) =>
      failures.has(docId) || !RESPONSES[docId]
        ? { docId, record: null, error: failures.get(docId) ?? axiosError(404) }
        : { docId, record: toDocumentRecord(RESPONSES[docId]), error: null },
    ),
  )
}

// --- Mounting --------------------------------------------------------------------------------------

let pinia: Pinia
let stream: ProjectEventStream
let wrapper: VueWrapper<any> | null = null
let windowOpen: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  stream = installProjectEventStream()
  failures.clear()
  detailRead.mockReset()
  relationsRead.mockReset()
  serveDocuments()
  windowOpen = vi.spyOn(window, 'open').mockReturnValue(null)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  windowOpen.mockRestore()
  vi.useRealTimers()
})

async function mountTab(target: InspectorTarget) {
  wrapper = mount(DocumentsTab, { props: { target }, global: { plugins: [i18n, pinia] } })
  await flushPromises()
  return wrapper
}

const find = (w: VueWrapper<any>, test: string) => w.find(`[data-test="${test}"]`)
const texts = (w: VueWrapper<any>, test: string) => w.findAll(`[data-test="${test}"]`).map((item) => item.text())
const paneState = (w: VueWrapper<any>) => w.find('[data-test="inspector-pane-state"]')

describe('documents tab — real-shaped data of the fixtures', () => {
  it('v01: the read document, its rejection reason and history, the AI review, the v0.1 slot and the superseded document', async () => {
    const w = await mountTab(targetOf(SELECTIONS.v01))
    expect(detailRead).toHaveBeenCalledWith(V01_DOC)
    expect(relationsRead).toHaveBeenCalledWith([V01_PREVIOUS])

    expect(find(w, 'documents-current').attributes('data-doc')).toBe(V01_DOC)
    expect(find(w, 'documents-current-type').text()).toBe('TR')
    expect(find(w, 'documents-current-seq').text()).toBe('#11')
    expect(find(w, 'documents-current-title').text()).toBe('작업레포트 — T#1')
    expect(find(w, 'documents-current-review').text()).toBe('반려됨')
    expect(find(w, 'documents-current-review').attributes('data-tone')).toBe('danger')
    expect(find(w, 'documents-current-status').text()).toBe('문서 상태 open')
    expect(find(w, 'documents-current-revision').text()).toBe('rev 2')
    expect(find(w, 'documents-current-ai-review').text()).toBe('AI 검수 이력 있음')
    expect(find(w, 'documents-rejection-reason').text()).toBe('반려 사유: 구조 검사 누락')

    expect(texts(w, 'documents-rejection')).toEqual(['2026-10-09 15:00구조 검사 누락', '2026-10-09 14:10테스트 결과 누락AI 응답: 테스트 결과를 추가했습니다.'])

    expect(find(w, 'documents-slot').exists()).toBe(true)
    expect(find(w, 'documents-slot-order').text()).toBe('4')
    expect(find(w, 'documents-slot-type').text()).toBe('TR')
    expect(find(w, 'documents-slot-label').text()).toBe('T#1 결과')
    expect(find(w, 'documents-slot-derived_status').text()).toBe('in_progress')
    expect(find(w, 'documents-slot-wp_card').text()).toBe('c_t1')
    expect(find(w, 'documents-slot-protected').exists()).toBe(false)
    expect(find(w, 'documents-run').exists()).toBe(false)

    const previous = w.findAll('[data-test="documents-previous-item"]')
    expect(previous.map((item) => [item.attributes('data-doc'), item.attributes('data-state')])).toEqual([[V01_PREVIOUS, 'ready']])
    expect(previous[0].text()).toContain('작업레포트 — T#1 (1차)')
    expect(previous[0].text()).toContain('#9')

    expect(paneState(w).exists()).toBe(false)
    expect(find(w, 'documents-edit-hint').text()).toBe('문서 작성·편집은 ↗ 열기로 v0.1 문서 화면에서 합니다.')
  })

  it('graph: the read document and the Graph node run; no slot, no rejection, no previous document', async () => {
    const w = await mountTab(targetOf(SELECTIONS.graph))
    expect(detailRead).toHaveBeenCalledWith(GRAPH_DOC)
    expect(relationsRead).toHaveBeenCalledWith([])
    expect(find(w, 'documents-current-title').text()).toBe('테스트 지시')
    expect(find(w, 'documents-current-seq').text()).toBe('#7')
    expect(find(w, 'documents-current-review').text()).toBe('승인됨')
    expect(find(w, 'documents-current-ai-review').exists()).toBe(false)
    expect(find(w, 'documents-rejection-reason').exists()).toBe(false)
    expect(find(w, 'documents-run-status').text()).toBe('completed')
    expect(find(w, 'documents-run-opened_at').text()).toBe('2026-10-09 01:00')
    expect(find(w, 'documents-run-finished_at').text()).toBe('2026-10-09 01:20')
    expect(find(w, 'documents-run-result_doc').text()).toBe(GRAPH_DOC)
    expect(find(w, 'documents-run-open').exists()).toBe(false)
    expect(find(w, 'documents-slot').exists()).toBe(false)
    expect(find(w, 'documents-rejections').exists()).toBe(false)
    expect(find(w, 'documents-previous').exists()).toBe(false)
  })

  it('before the read answers: the Canvas values and the loading state', async () => {
    detailRead.mockImplementation(() => new Promise(() => {}))
    const w = await mountTab(targetOf(SELECTIONS.v01))
    expect(find(w, 'documents-current-title').text()).toBe('T#1 결과 레포트')
    expect(find(w, 'documents-current-review').text()).toBe('검수 대기')
    expect(find(w, 'documents-current-seq').exists()).toBe(false)
    expect(paneState(w).attributes('data-state')).toBe('loading')
    expect(w.findAll('[data-test="documents-previous-item"]').map((item) => item.attributes('data-state'))).toEqual(['pending'])
    expect(find(w, 'documents-open').exists()).toBe(true)
  })

  it('no pair slot, result slot or write line is drawn (D#1 §8.4)', async () => {
    const w = await mountTab(targetOf(SELECTIONS.v01))
    expect(w.text()).not.toMatch(/작성\s*$|결과 슬롯|지시서/)
    expect(w.findAll('button').map((button) => button.text())).toEqual(['↗ 열기', '↗ 열기'])
  })
})

describe('documents tab — ↗ 열기', () => {
  it('opens the current and a previous document through openDocument: new tab, noopener, doc_id only', async () => {
    const w = await mountTab(targetOf(SELECTIONS.v01))
    await find(w, 'documents-open').trigger('click')
    expect(windowOpen).toHaveBeenLastCalledWith(`/main/?doc=${encodeURIComponent(V01_DOC)}`, '_blank', 'noopener')
    await find(w, 'documents-previous-open').trigger('click')
    expect(windowOpen).toHaveBeenLastCalledWith(`/main/?doc=${encodeURIComponent(V01_PREVIOUS)}`, '_blank', 'noopener')
    expect(windowOpen).toHaveBeenCalledTimes(2)
  })

  it('a Graph run result other than the bound document can be opened too', async () => {
    const selection: FlowGraphSelection = {
      ...SELECTIONS.graph,
      node: { ...SELECTIONS.graph.node, nodeRun: { status: 'completed', openedAt: null, finishedAt: null, resultDocId: 'flowgate.v02.0049.0008-TSR' } },
    }
    const w = await mountTab(targetOf(selection))
    await find(w, 'documents-run-open').trigger('click')
    expect(windowOpen).toHaveBeenLastCalledWith('/main/?doc=flowgate.v02.0049.0008-TSR', '_blank', 'noopener')
  })

  it.each([
    ['conflict (read only)', SELECTIONS.conflict],
    ['graph revision view (read only)', { ...SELECTIONS.graph, graph: { ...SELECTIONS.graph.graph, viewMode: 'revision' as const } }],
    ['unassigned', SELECTIONS.unassigned],
  ])('%s: ↗ 열기 stays enabled (a query)', async (_label, selection) => {
    const w = await mountTab(targetOf(selection))
    const button = find(w, 'documents-open')
    expect(button.attributes('disabled')).toBeUndefined()
    await button.trigger('click')
    expect(windowOpen).toHaveBeenCalledWith(`/main/?doc=${encodeURIComponent(targetOf(selection).docId!)}`, '_blank', 'noopener')
  })
})

describe('documents tab — failed reads', () => {
  it.each([
    ['404', axiosError(404), '문서를 찾을 수 없습니다.'],
    ['403', axiosError(403, 'permission_denied'), '이 문서를 볼 권한이 없습니다.'],
    ['409', axiosError(409, 'GROUP_DISPOSED'), '문서 정보를 불러오지 못했습니다.'],
    ['network', networkError(), '서버에 연결하지 못했습니다.'],
  ])('%s: the reason with a retry; the Canvas values and ↗ 열기 stay', async (_label, error, message) => {
    failures.set(V01_DOC, error)
    const w = await mountTab(targetOf(SELECTIONS.v01))
    expect(paneState(w).attributes('data-state')).toBe('error')
    expect(paneState(w).text()).toContain(message)
    expect(find(w, 'documents-current-title').text()).toBe('T#1 결과 레포트')
    expect(find(w, 'documents-open').exists()).toBe(true)

    failures.delete(V01_DOC)
    await find(w, 'inspector-pane-retry').trigger('click')
    await flushPromises()
    expect(detailRead).toHaveBeenCalledTimes(2)
    expect(paneState(w).exists()).toBe(false)
    expect(find(w, 'documents-current-title').text()).toBe('작업레포트 — T#1')
  })

  it('a failed read again (after an event) keeps the last data shown with the reason', async () => {
    vi.useFakeTimers()
    const w = await mountTab(targetOf(SELECTIONS.v01))
    stream.open()
    failures.set(V01_DOC, networkError())
    stream.emit('doc_review_status_changed', { docId: V01_DOC })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(detailRead).toHaveBeenCalledTimes(2)
    expect(paneState(w).attributes('data-state')).toBe('error')
    expect(paneState(w).text()).toContain('서버에 연결하지 못했습니다.')
    expect(find(w, 'documents-current-title').text()).toBe('작업레포트 — T#1')
    expect(find(w, 'documents-rejection-reason').exists()).toBe(true)
  })

  it('a superseded document that cannot be read stays listed by id; the rest of the tab is shown', async () => {
    failures.set(V01_PREVIOUS, axiosError(404))
    const w = await mountTab(targetOf(SELECTIONS.v01))
    const item = w.get('[data-test="documents-previous-item"]')
    expect(item.attributes('data-state')).toBe('missing')
    expect(item.text()).toContain(V01_PREVIOUS)
    expect(find(w, 'documents-previous-missing').text()).toBe('불러오지 못한 문서')
    expect(paneState(w).exists()).toBe(false)
    expect(find(w, 'documents-current-title').text()).toBe('작업레포트 — T#1')
  })
})

describe('documents tab — server events and target changes', () => {
  it('reads again on its document events (coalesced) and shows the new state; other documents are ignored', async () => {
    vi.useFakeTimers()
    RESPONSES[V01_DOC] = { ...V01_RESPONSE, doc_review_status: 'pending_review', rejection_reason: null, rejection_history: [] }
    try {
      const w = await mountTab(targetOf(SELECTIONS.v01))
      stream.open()
      expect(find(w, 'documents-current-review').text()).toBe('검수 대기')
      expect(detailRead).toHaveBeenCalledTimes(1)

      stream.emit('doc_review_status_changed', { docId: 'flowgate.v02.0048.0013-TR', groupId: 'flowgate.v02.0048' })
      await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
      await flushPromises()
      expect(detailRead).toHaveBeenCalledTimes(1)

      RESPONSES[V01_DOC] = V01_RESPONSE
      stream.emit('doc_review_status_changed', { docId: V01_DOC, groupId: 'flowgate.v02.0048' })
      stream.emit('ai_review_arrived', { docId: V01_DOC, groupId: 'flowgate.v02.0048' })
      await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
      await flushPromises()
      expect(detailRead).toHaveBeenCalledTimes(2)
      expect(find(w, 'documents-current-review').text()).toBe('반려됨')
      expect(find(w, 'documents-rejection-reason').text()).toBe('반려 사유: 구조 검사 누락')

      stream.emit('document_explorer_refresh', { groupId: 'flowgate.v02.0048' })
      await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
      await flushPromises()
      expect(detailRead).toHaveBeenCalledTimes(3)
    } finally {
      RESPONSES[V01_DOC] = V01_RESPONSE
    }
  })

  it('listens to the document events of D#1 §7.4 only', async () => {
    await mountTab(targetOf(SELECTIONS.v01))
    const [connection] = stream.connections()
    expect([...DOCUMENTS_EVENTS]).toEqual(['doc_review_status_changed', 'ai_review_arrived', 'document_explorer_refresh'])
    for (const event of DOCUMENTS_EVENTS) expect(connection.listened(event), event).toBe(1)
    expect(connection.listened('qna_q_registered')).toBe(0)
  })

  it("a slow answer for the previous Node never covers the new Node's document", async () => {
    const pending: Array<{ docId: string; resolve: () => void }> = []
    detailRead.mockImplementation(
      (docId: string) => new Promise((resolve) => pending.push({ docId, resolve: () => resolve(toDocumentDetail(RESPONSES[docId])) })),
    )
    const w = await mountTab(targetOf(SELECTIONS.v01))
    await w.setProps({ target: targetOf(SELECTIONS.graph) })
    await flushPromises()
    pending.find((call) => call.docId === GRAPH_DOC)!.resolve()
    await flushPromises()
    pending.find((call) => call.docId === V01_DOC)!.resolve()
    await flushPromises()
    expect(find(w, 'documents-current').attributes('data-doc')).toBe(GRAPH_DOC)
    expect(find(w, 'documents-current-title').text()).toBe('테스트 지시')
    expect(find(w, 'documents-slot').exists()).toBe(false)
    expect(find(w, 'documents-rejections').exists()).toBe(false)
  })
})

describe('documents tab in the Frame (real registry)', () => {
  async function mountFrame(selection: FlowGraphSelection) {
    wrapper = mount(DesignerInspector, { props: { selection }, global: { plugins: [i18n, pinia] } })
    await flushPromises()
    return wrapper
  }

  const tabLabels = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').map((tab) => tab.text())

  it.each([
    ['v01', SELECTIONS.v01, V01_DOC],
    ['graph', SELECTIONS.graph, GRAPH_DOC],
    ['unassigned', SELECTIONS.unassigned, 'flowgate.v02.0050.0001-R'],
    ['conflict', SELECTIONS.conflict, 'flowgate.v02.0051.0003-D'],
  ])('%s document Node: the 문서 tab holds the documents section', async (_label, selection, docId) => {
    const w = await mountFrame(selection)
    expect(tabLabels(w)).toContain('문서')
    const section = w.get('[data-test="designer-inspector-section"][data-key="documents"]')
    expect(section.get('[data-test="documents-current"]').attributes('data-doc')).toBe(docId)
  })

  it.each([
    ['v01 slot without a document', SELECTIONS.v01NoDocument],
    ['graph gate', SELECTIONS.graphGate],
  ])('%s: no documents section (the header only, D#1 §4.4)', async (_label, selection) => {
    const w = await mountFrame(selection)
    expect(w.find('[data-test="designer-inspector-section"][data-key="documents"]').exists()).toBe(false)
    expect(w.find('[data-test="inspector-documents"]').exists()).toBe(false)
    expect(detailRead).not.toHaveBeenCalled()
  })
})
