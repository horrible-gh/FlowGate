/**
 * flowgate.v02.0056 F2-2 — the three states of a card line and its row actions (O3 0009-TR §4-1 · §4-2 · §4-3), on
 * the real Frame and registry with the document windows mounted and `@shared/api` mocked:
 * - slotStateView / slotFill / slotLineCard decide once: no document → "문서 없음" + [작성]; a document without a body
 *   → "비어 있음" (no words since 0017-CH: a dashed line with a hollow document icon, "비어 있음" its hover text;
 *   opened straight in its editor); else a usual line. M and WP lines are never "비어 있음";
 * - the history button carries `history_count`, its hover text the AI review / rejection split;
 * - [작성] opens the line's write window and nothing is made until [저장]: the 201 line stands for the row at once,
 *   the Canvas is read again (refresh-flow), a result bound to the running card is the F2-1 승인 대기 after it;
 * - 409 slot_has_document / node_token_unavailable read the Canvas again and open that document; waiting refusals
 *   and the 422 content reasons are told with the server's message;
 * - [AI 호출] opens the document's AI call window without calling the AI;
 * - a read-only view or a card not saved yet offers no row action.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import {
  SLOT_ALWAYS_FILLED_TYPES,
  SLOT_WRITABLE_SLOTS,
  slotFill,
  slotLineCard,
  slotStateView,
} from '@/features/designer/inspector/parts/slotStateView'
import { DocumentWindows, closeAllDocumentWindows, documentAiWindowList, documentWindowList, slotWriteWindowList, writtenCardLines } from '@/features/documentWindow'
import { prepareInspectorI18n, selectionOf } from './support/inspectorSupport'

const api = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
  patchRequest: vi.fn(),
  deleteRequest: vi.fn(),
}))
const settingsNodeRead = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, ...api }
})

vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return { ...actual, settingsNodeRead }
})

const i18n = prepareInspectorI18n()

const FLOW = 'flowgate.v02.0056'
const doc = (seq: string, type: string) => `${FLOW}.${seq}-${type}`
const offline = () => Promise.reject(Object.assign(new Error('offline'), { isAxiosError: true, request: {} }))
const refusal = (status: number, code: string, message = '', params: Record<string, unknown> = {}) =>
  Object.assign(new Error(code), { isAxiosError: true, response: { status, data: { ok: false, error: { code, message, params } } } })

let frame: VueWrapper<any> | null = null
let windows: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  for (const fn of Object.values(api)) {
    fn.mockReset()
    fn.mockImplementation(offline)
  }
  settingsNodeRead.mockReset()
  settingsNodeRead.mockResolvedValue({ cardKind: 'run', locked: false, fields: { instruction: { value: 'use', stored: null, default: 'use', choices: ['use', 'omit'] } } })
  closeAllDocumentWindows()
  windows = mount(DocumentWindows, { attachTo: document.body, global: { plugins: [i18n] } })
})

afterEach(() => {
  frame?.unmount()
  frame = null
  windows?.unmount()
  windows = null
  closeAllDocumentWindows()
  document.body.innerHTML = ''
})

function line(slot: string, docType: string, docId: string | null, fields: Partial<FlowGraphCardDocument> = {}): FlowGraphCardDocument {
  return { slot, docType, author: slot === 'report' ? 'run' : 'human', docId, title: `카드 · ${slot}`, status: docId ? 'draft' : null, docReviewStatus: null, ...fields }
}

function card(key: string, docType: string, lines: FlowGraphCardDocument[], fields: Partial<FlowGraphNode> = {}): FlowGraphNode {
  return {
    id: key,
    kind: 'task',
    title: '작업 카드',
    state: 'draft',
    docType,
    x: 0,
    y: 0,
    definition: { nodeType: 'document', docType, paletteItemId: null, label: '작업 카드', assignee: null, rootBound: false },
    document: null,
    cardDocuments: lines,
    ...fields,
  }
}

function graphOf(node: FlowGraphNode, viewMode: string = 'draft'): FlowGraph {
  return { flowId: FLOW, nodes: [node], edges: [], ownership: 'graph', viewMode, draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: 1 } }
}

async function mountFrame(node: FlowGraphNode, viewMode?: string) {
  frame = mount(DesignerInspector, { attachTo: document.body, props: { selection: selectionOf(graphOf(node, viewMode), node.id) }, global: { plugins: [i18n] } })
  await flushPromises()
  return frame
}

async function reselect(node: FlowGraphNode) {
  await frame!.setProps({ selection: selectionOf(graphOf(node), node.id) })
  await flushPromises()
}

const rows = () => frame!.findAll('[data-test="designer-inspector-body"] [data-test="inspector-slot"]')
const row = (slot: string) => frame!.get(`[data-test="designer-inspector-body"] [data-test="inspector-slot"][data-slot="${slot}"]`)
// 0017-CH: [AI 호출] is an icon button — its words are its tooltip.
const actions = (slot: string) => row(slot).findAll('[data-test="inspector-slot-action"]').map((button) => [button.attributes('data-action'), button.text() || button.attributes('title')])
const hostIntents = () => (frame!.emitted('host') ?? []).map(([intent]) => intent)
const body = (selector: string) => document.body.querySelector<HTMLElement>(selector)
const text = (node: Element | null | undefined) => node?.textContent?.replace(/\s+/g, ' ').trim() ?? null
async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}
async function typeInto(selector: string, value: string) {
  const area = body(selector) as HTMLTextAreaElement | null
  if (!area) throw new Error(`no ${selector}`)
  area.value = value
  area.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}

/** A task card: the 지시서 written but empty (no body), the 레포트 without a document. */
const TASK = card('gn_t', 'T', [
  line('instruction', 'T', doc('0011', 'T'), { hasBody: false, reviewCount: 0, rejectionCount: 0, historyCount: 0 }),
  line('report', 'TR', null, { hasBody: false, reviewCount: 0, rejectionCount: 0, historyCount: 0 }),
])

describe('the slot rules (parts/slotStateView, flowgate.v02.0056 F2-2)', () => {
  it('slotFill: no document → missing, has_body false → blank, else filled; M and WP are never blank; an unsent has_body is filled', () => {
    expect(slotFill(null)).toBe('missing')
    expect(slotFill({ slot: 'report', docType: 'TR', docId: null, hasBody: false })).toBe('missing')
    expect(slotFill({ slot: 'report', docType: 'TR', docId: 'd', hasBody: false })).toBe('blank')
    expect(slotFill({ slot: 'report', docType: 'TR', docId: 'd', hasBody: true })).toBe('filled')
    expect(slotFill({ slot: 'report', docType: 'TR', docId: 'd' })).toBe('filled')
    expect(SLOT_ALWAYS_FILLED_TYPES).toEqual(['M', 'WP'])
    expect(slotFill({ slot: 'plan', docType: 'WP', docId: 'd', hasBody: false })).toBe('filled')
    expect(slotFill({ slot: 'deploy', docType: 'm', docId: 'd', hasBody: false })).toBe('filled')
  })

  it('slotLineCard: [작성] on a missing writable line of a changeable card; blank · history · [AI 호출] on the line of its document', () => {
    const changeable = { changeable: true, running: () => false }
    expect(SLOT_WRITABLE_SLOTS).toEqual(['requirement', 'delegation', 'plan', 'deploy', 'instruction', 'report'])
    expect(slotLineCard({ id: null, empty: true }, { slot: 'report', docType: 'TR', docId: null }, changeable)).toEqual({
      cardSlot: 'report',
      blank: false,
      historyCount: null,
      historyDetail: null,
      actions: [{ key: 'write', state: 'enabled' }],
    })
    // The 시나리오 line shares the 지시서 document: no [작성] of its own.
    expect(slotLineCard({ id: null, empty: true }, { slot: 'scenario', docType: 'TS', docId: null }, changeable).actions).toEqual([])
    expect(slotLineCard({ id: null, empty: true }, { slot: 'report', docType: 'TR', docId: null }, { ...changeable, changeable: false }).actions).toEqual([])
    expect(
      slotLineCard({ id: 'd', empty: false }, { slot: 'report', docType: 'TR', docId: 'd', hasBody: false, reviewCount: 2, rejectionCount: 1, historyCount: 3 }, changeable),
    ).toEqual({ cardSlot: 'report', blank: true, historyCount: 3, historyDetail: { reviews: 2, rejections: 1 }, actions: [{ key: 'ai_invoke', state: 'enabled' }] })
    expect(slotLineCard({ id: 'd', empty: false }, { slot: 'report', docType: 'TR', docId: 'd' }, { changeable: true, running: (id) => id === 'd' }).actions).toEqual([
      { key: 'ai_invoke', state: 'running' },
    ])
    // A row showing another document than the card line's, and a row without a card line, get nothing.
    expect(slotLineCard({ id: 'other', empty: false }, { slot: 'report', docType: 'TR', docId: 'd' }, changeable).actions).toEqual([])
    expect(slotLineCard({ id: 'd', empty: false }, undefined, changeable)).toEqual({ cardSlot: null, blank: false, historyCount: null, historyDetail: null, actions: [] })
  })

  it('slotStateView: a blank line draws no badge and no words (its hover text says it); an empty line still wins', () => {
    expect(slotStateView({ label: '검수 대기', tone: 'warning' }, false, null, '비어 있음')).toEqual({ kind: 'blank', tone: 'none', text: '', title: '비어 있음', quiet: false })
    expect(slotStateView(null, true, '문서 없음', '비어 있음')).toMatchObject({ kind: 'empty', text: '문서 없음' })
    expect(slotStateView({ label: '검수 대기', tone: 'warning' }, false, null, null)).toMatchObject({ kind: 'badge' })
  })
})

describe('the three states on the documents tab (flowgate.v02.0056 F2-2)', () => {
  it('비어 있음: no words — a dashed line, the hollow document icon, the hover text; the name and the icon open the editor', async () => {
    await mountFrame(TASK)
    const instruction = row('instruction')
    expect(instruction.attributes('data-blank')).toBe('true')
    expect(instruction.classes()).toContain('is-blank')
    expect(instruction.attributes('title')).toBe('비어 있음')
    expect(instruction.find('[data-test="inspector-slot-state"]').exists()).toBe(false)
    expect(instruction.text()).not.toContain('비어 있음')
    expect(instruction.get('[data-test="inspector-slot-open"]').attributes('data-icon')).toBe('document-blank')
    await instruction.get('[data-test="inspector-slot-name"]').trigger('click')
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([[doc('0011', 'T'), 'edit']])
    closeAllDocumentWindows()
    await row('instruction').get('[data-test="inspector-slot-open"]').trigger('click')
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([[doc('0011', 'T'), 'edit']])
  })

  it('문서 없음 + [작성]; a usual line has neither; M and WP lines are never 비어 있음', async () => {
    await mountFrame(TASK)
    const report = row('report')
    expect(report.attributes('data-empty')).toBe('true')
    expect(report.get('[data-test="inspector-slot-state"]').text()).toBe('문서 없음')
    expect(actions('report')).toEqual([['write', '작성']])
    frame!.unmount()
    frame = null
    await mountFrame(card('gn_t', 'T', [line('instruction', 'T', doc('0011', 'T'), { hasBody: true }), line('report', 'TR', doc('0012', 'TR'), { hasBody: true })]))
    expect(rows().map((item) => item.attributes('data-blank') ?? 'false')).toEqual(['false', 'false'])
    expect(rows().map((item) => item.find('[data-test="inspector-slot-state"]').exists())).toEqual([false, false])
    expect(actions('report')).toEqual([['ai_invoke', 'AI 호출']])
    frame!.unmount()
    frame = null
    const plan = { ...card('gn_wp', 'WP', [line('plan', 'WP', doc('0003', 'WP'), { hasBody: false }), line('deploy', 'M', doc('0004', 'M'), { hasBody: false })]), kind: 'plan' }
    await mountFrame(plan)
    expect(rows().map((item) => item.attributes('data-blank') ?? 'false')).toEqual(['false', 'false'])
  })

  it('the history button carries history_count; its hover text has the AI review / rejection split', async () => {
    await mountFrame(card('gn_t', 'T', [line('instruction', 'T', doc('0011', 'T'), { hasBody: true, reviewCount: 2, rejectionCount: 1, historyCount: 3 }), line('report', 'TR', null)]))
    const history = row('instruction').get('[data-test="inspector-slot-history"]')
    expect(history.get('[data-test="inspector-history-count"]').text()).toBe('3')
    expect(history.attributes('title')).toBe('검수·반려 이력 3건 (AI 검수 2 · 반려 1)')
    i18n.global.locale.value = 'en'
    await flushPromises()
    expect(row('instruction').get('[data-test="inspector-slot-history"]').attributes('title')).toBe('Review history · 3 (AI reviews 2 · rejections 1)')
  })

  it('no history count when the server sent none or 0 (the common wording)', async () => {
    await mountFrame(card('gn_t', 'T', [line('instruction', 'T', doc('0011', 'T')), line('report', 'TR', doc('0012', 'TR'), { historyCount: 0, reviewCount: 0, rejectionCount: 0 })]))
    expect(row('instruction').find('[data-test="inspector-history-count"]').exists()).toBe(false)
    expect(row('report').find('[data-test="inspector-history-count"]').exists()).toBe(false)
  })

  it('a read-only view (a revision) and a card not saved yet offer no row action', async () => {
    await mountFrame(TASK, 'revision')
    expect(actions('report')).toEqual([])
    expect(actions('instruction')).toEqual([])
    frame!.unmount()
    frame = null
    await mountFrame({ ...TASK, id: 'tmp_new' })
    expect(actions('report')).toEqual([])
  })
})

describe('[작성] (flowgate.v02.0056 F2-2, O3 §4-2)', () => {
  const WRITTEN = { slot: 'report', doc_type: 'TR', author: 'run', doc_id: doc('0021', 'TR'), title: '레포트', status: 'open', doc_review_status: 'pending_review', has_body: true, review_count: 0, rejection_count: 0, history_count: 0 }

  async function openWrite() {
    await mountFrame(TASK)
    await row('report').get('[data-test="inspector-slot-action"][data-action="write"]').trigger('click')
    await flushPromises()
    expect(slotWriteWindowList().map((win) => win.key)).toEqual([`${FLOW}\ngn_t\nreport`])
    return body('[data-test="slot-write-window"]')!
  }

  it('opens the write window of the line; nothing is made until [저장]', async () => {
    const win = await openWrite()
    expect(win.getAttribute('data-slot')).toBe('report')
    expect(text(body('[data-test="slot-write-heading"]'))).toContain('레포트 작성')
    expect(text(body('[data-test="slot-write-card"]'))).toBe('작업 카드')
    expect(api.postRequest).not.toHaveBeenCalled()
  })

  it('[저장]: posts the text, the 201 line stands for the row at once, the Canvas is read again, the window closes', async () => {
    await openWrite()
    api.postRequest.mockResolvedValue({ data: { ok: true, flow_id: FLOW, node_key: 'gn_t', slot: 'report', doc_type: 'TR', doc_id: WRITTEN.doc_id, bound_as_result: true, line: WRITTEN } })
    await typeInto('[data-test="slot-write-textarea"]', '# 결과\n본문')
    await click(body('[data-test="slot-write-save"]'))
    expect(api.postRequest).toHaveBeenCalledWith(`/api/v1/flows/${FLOW}/nodes/gn_t/slots/report/document`, { content: '# 결과\n본문' })
    expect(slotWriteWindowList()).toEqual([])
    expect(hostIntents()).toContainEqual({ kind: 'refresh-flow', flowId: FLOW })
    // Before any Canvas read: the row shows the written document, its state from the 201 line.
    const report = row('report')
    expect(report.attributes('data-doc')).toBe(WRITTEN.doc_id)
    expect(report.attributes('data-empty')).toBe('false')
    expect(report.get('[data-test="inspector-slot-state"]').text()).toBe('검수 대기')
    expect(writtenCardLines(FLOW, 'gn_t')).toHaveLength(1)
    // The Canvas read shows it: the written line is settled.
    await reselect(card('gn_t', 'T', [TASK.cardDocuments![0], line('report', 'TR', WRITTEN.doc_id, { status: 'open', docReviewStatus: 'pending_review', hasBody: true })]))
    expect(writtenCardLines(FLOW, 'gn_t')).toEqual([])
    expect(row('report').attributes('data-doc')).toBe(WRITTEN.doc_id)
  })

  it('a result bound to the running card: after the Canvas read the card waits for its approval (the F2-1 bar)', async () => {
    const running = { ...TASK, nodeRun: { status: 'open', openedAt: null, finishedAt: null, resultDocId: null, awaitingApproval: false, stage: 'result' } }
    await mountFrame(running)
    await row('report').get('[data-test="inspector-slot-action"][data-action="write"]').trigger('click')
    await flushPromises()
    expect(body('[data-test="slot-write-result-note"]')).not.toBeNull()
    api.postRequest.mockResolvedValue({ data: { ok: true, doc_id: WRITTEN.doc_id, bound_as_result: true, line: WRITTEN } })
    await typeInto('[data-test="slot-write-textarea"]', '결과')
    await click(body('[data-test="slot-write-save"]'))
    await reselect({
      ...running,
      cardDocuments: [TASK.cardDocuments![0], line('report', 'TR', WRITTEN.doc_id, { status: 'open', docReviewStatus: 'pending_review', hasBody: true })],
      nodeRun: { status: 'review', openedAt: null, finishedAt: null, resultDocId: WRITTEN.doc_id, awaitingApproval: true, stage: 'result' },
    })
    expect(frame!.get('[data-test="inspector-card-approval"]').text()).toContain('승인 대기')
  })

  it('a blank text makes an empty draft (content null)', async () => {
    await openWrite()
    api.postRequest.mockResolvedValue({ data: { ok: true, doc_id: 'd', bound_as_result: false, line: null } })
    await typeInto('[data-test="slot-write-textarea"]', '   ')
    await click(body('[data-test="slot-write-save"]'))
    expect(api.postRequest.mock.calls[0][1]).toEqual({ content: null })
  })

  it('a work plan line is made as an empty plan: no text, content null', async () => {
    await mountFrame({ ...card('gn_wp', 'WP', [line('plan', 'WP', null), line('deploy', 'M', null)]), kind: 'plan' })
    expect(actions('plan')).toEqual([['write', '작성']])
    await row('plan').get('[data-test="inspector-slot-action"]').trigger('click')
    await flushPromises()
    expect(body('[data-test="slot-write-textarea"]')).toBeNull()
    expect(text(body('[data-test="slot-write-contentless"]'))).toBe('빈 작업계획을 만듭니다. 내용은 만든 뒤 편집합니다.')
    api.postRequest.mockResolvedValue({ data: { ok: true, doc_id: 'd', bound_as_result: false, line: null } })
    await click(body('[data-test="slot-write-save"]'))
    expect(api.postRequest).toHaveBeenCalledWith(`/api/v1/flows/${FLOW}/nodes/gn_wp/slots/plan/document`, { content: null })
  })

  it('409 slot_has_document: the Canvas is read again and that document opens; the text stays, nothing more is written', async () => {
    await openWrite()
    api.postRequest.mockRejectedValue(refusal(409, 'slot_has_document', '이 행에는 이미 문서가 있습니다', { doc_id: doc('0030', 'TR') }))
    await typeInto('[data-test="slot-write-textarea"]', '내 글')
    await click(body('[data-test="slot-write-save"]'))
    expect(documentWindowList().map((win) => win.docId)).toEqual([doc('0030', 'TR')])
    expect(hostIntents()).toContainEqual({ kind: 'refresh-flow', flowId: FLOW })
    expect(body('[data-test="slot-write-notice"]')!.getAttribute('data-key')).toBe('slot_has_document')
    expect((body('[data-test="slot-write-textarea"]') as HTMLTextAreaElement).value).toBe('내 글')
    expect((body('[data-test="slot-write-save"]') as HTMLButtonElement).disabled).toBe(true)
  })

  it('409 node_token_unavailable (no document named): the document opens once the Canvas read shows it', async () => {
    await openWrite()
    api.postRequest.mockRejectedValue(refusal(409, 'node_token_unavailable', ''))
    await typeInto('[data-test="slot-write-textarea"]', '내 글')
    await click(body('[data-test="slot-write-save"]'))
    expect(documentWindowList()).toEqual([])
    expect(text(body('[data-test="slot-write-notice"]'))).toBe('이 카드의 결과가 이미 등록되었습니다. 등록된 문서를 엽니다.')
    await reselect(card('gn_t', 'T', [TASK.cardDocuments![0], line('report', 'TR', doc('0031', 'TR'))]))
    expect(documentWindowList().map((win) => win.docId)).toEqual([doc('0031', 'TR')])
  })

  it.each([
    ['node_in_instruction_stage', '지시 AI가 지시서를 쓰는 중입니다. 끝난 뒤 다시 저장하세요.'],
    ['node_ai_working', '카드의 AI가 작업 중입니다. 끝난 뒤 다시 저장하세요.'],
    ['flow_disposed', '폐기된 플로우라 작성할 수 없습니다.'],
  ])('409 %s: told in the window with the server message; the window stays', async (code, words) => {
    await openWrite()
    api.postRequest.mockRejectedValue(refusal(409, code, '서버 안내'))
    await typeInto('[data-test="slot-write-textarea"]', '본문')
    await click(body('[data-test="slot-write-save"]'))
    expect(text(body('[data-test="slot-write-notice"]'))).toBe(`${words} — 서버 안내`)
    expect(slotWriteWindowList()).toHaveLength(1)
    expect((body('[data-test="slot-write-save"]') as HTMLButtonElement).disabled).toBe(false)
    expect(documentWindowList()).toEqual([])
  })

  it('422 empty_body: under the text; a new edit clears it', async () => {
    await openWrite()
    api.postRequest.mockRejectedValue(
      Object.assign(new Error('422'), {
        isAxiosError: true,
        response: { status: 422, data: { ok: false, error: { code: 'invalid_request', message: '', params: { errors: [{ field: 'content', reason: 'empty_body' }] } } } },
      }),
    )
    await click(body('[data-test="slot-write-save"]'))
    expect(text(body('[data-test="slot-write-field-error"]'))).toBe('결과로 등록되는 문서는 본문이 필요합니다.')
    await typeInto('[data-test="slot-write-textarea"]', '본문')
    expect(body('[data-test="slot-write-field-error"]')).toBeNull()
  })
})

describe('[AI 호출] on a document line (flowgate.v02.0056 F2-2, O3 §4-3)', () => {
  it('opens the document AI call window; nothing is started by opening it', async () => {
    api.getRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v1/ai-invoke/providers') return { data: { providers: [{ id: 'aip_opus', name: 'Opus' }], default_provider_id: 'aip_opus' } }
      if (path === '/api/v1/ai-invoke/active') return { data: { active: false } }
      return offline()
    })
    await mountFrame(card('gn_t', 'T', [line('instruction', 'T', doc('0011', 'T'), { hasBody: true }), line('report', 'TR', null)]))
    expect(actions('instruction')).toEqual([['ai_invoke', 'AI 호출']])
    expect(row('instruction').get('[data-action="ai_invoke"]').attributes('data-icon')).toBe('robot')
    expect(row('instruction').get('[data-action="ai_invoke"]').attributes('aria-label')).toBe('AI 호출')
    await row('instruction').get('[data-test="inspector-slot-action"][data-action="ai_invoke"]').trigger('click')
    await flushPromises()
    expect(documentAiWindowList().map((win) => [win.docId, win.request.groupId, win.request.name])).toEqual([[doc('0011', 'T'), FLOW, '지시서']])
    expect(body('[data-test="document-ai-window"]')).not.toBeNull()
    expect(api.postRequest).not.toHaveBeenCalled()
  })
})
