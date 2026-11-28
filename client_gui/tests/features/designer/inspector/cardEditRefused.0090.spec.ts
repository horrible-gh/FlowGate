/**
 * flowgate.v02test.0090 (0013-T §4.3) — every card EDIT entry point of the Inspector follows the server's refusal of
 * `project.document.update` / `.create` / `.delete`, in the same screen test as the button state and the reason:
 *
 * - a viewer (all three refused, `role_limit`, developer or above needed): ✎ of every card kind, [작성], the header memo,
 *   the memo window's [편집]/[저장]/[삭제] and the work plan window are off with the server's sentence as the reason, and a
 *   press that still arrives (stale click) changes nothing — no window, no event;
 * - a developer (nothing refused): the same buttons work;
 * - a reviewer (edit refused, approve on): ✎ is off, the decision button (approve) is untouched;
 * - no answer from the server yet: nothing is turned off (the server still checks every request).
 * The words are the shared denial sentences (ko / en / ja), never a role guess.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import { cardMemoCardOf } from '@/features/designer/inspector/features/cardMemo/useCardMemo'
import { closeAllCardMemoWindows, openCardMemoWindow } from '@/features/designer/inspector/features/cardMemo/useCardMemoWindows'
import { toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import InspectorSlotList, { type InspectorSlotView } from '@/features/designer/inspector/parts/InspectorSlotList.vue'
import { closeAllDocumentWindows, documentWindowList } from '@/features/documentWindow'
import { closeAllWorkPlanEditWindows, workPlanEditWindowList } from '@/features/workPlanWindow'
import { grantProject, resetCapabilities } from '../../../helpers/capabilities'
import { PROJECT_ID, prepareInspectorI18n, selectionOf } from './support/inspectorSupport'

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const offline = () => Promise.reject(Object.assign(new Error('offline'), { request: {} }))
  return { ...actual, getRequest: vi.fn(offline), postRequest: vi.fn(offline), putRequest: vi.fn(offline), patchRequest: vi.fn(offline), deleteRequest: vi.fn(offline) }
})
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return { ...actual, settingsNodeRead: vi.fn(() => Promise.reject(new Error('offline'))) }
})

const i18n = prepareInspectorI18n()
const FLOW = 'flowgate.v02.0060'
const VIEWER_SENTENCE = '이 프로젝트에서 보기 전용 역할이라 할 수 없습니다(개발자 이상 필요).'

const DEVELOPER_ONLY = { reason: 'role_limit', minimum_role: 'role_developer' }
const REFUSED_DOCUMENT = {
  'project.document.update': DEVELOPER_ONLY,
  'project.document.create': DEVELOPER_ONLY,
  'project.document.delete': DEVELOPER_ONLY,
}

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllDocumentWindows()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  closeAllDocumentWindows()
  closeAllWorkPlanEditWindows()
  closeAllCardMemoWindows()
  resetCapabilities()
  document.body.innerHTML = ''
})

const doc = (key: string, type: string) => `${FLOW}.${key}-${type}`

function line(slot: string, docType: string, docId: string | null, author: 'human' | 'run'): FlowGraphCardDocument {
  return { slot, docType, author, docId, title: `카드 · ${slot}`, status: 'draft', docReviewStatus: null, hasBody: false, bodyState: 'readable' }
}

function placed(key: string, nodeType: string, docType: string | null, lines: FlowGraphCardDocument[]): FlowGraphNode {
  const first = lines.find((candidate) => candidate.author === 'human') ?? lines[0]
  return {
    id: key, kind: 'task', title: '새 카드', state: 'draft', docType, x: 0, y: 0, structureEditable: true,
    definition: { nodeType, docType, paletteItemId: null, label: '새 카드', assignee: null, rootBound: false },
    document: first?.docId ? { docId: first.docId, typeCode: first.docType, seq: null, title: first.title, docReviewStatus: null, status: 'draft', supersedes: [] } : null,
    cardDocuments: lines,
  }
}

/** The card kinds whose documents tab draws ✎ (the test card's ✎ is its own button, below; the plan has none). */
const CARDS: readonly { card: string; node: FlowGraphNode }[] = [
  { card: '요구사항', node: placed('gn_r', 'document', 'R', [line('requirement', 'R', doc('0001', 'R'), 'human')]) },
  { card: '설계', node: placed('gn_d', 'document', 'D', [line('instruction', 'DS', doc('0005', 'DS'), 'human'), line('report', 'D', doc('0006', 'D'), 'run')]) },
  { card: '조사', node: placed('gn_n', 'document', 'N', [line('instruction', 'N', doc('0007', 'N'), 'human'), line('report', 'NR', doc('0008', 'NR'), 'run')]) },
  { card: '작업', node: placed('gn_t', 'document', 'T', [line('instruction', 'T', doc('0009', 'T'), 'human'), line('report', 'TR', doc('0010', 'TR'), 'run')]) },
  { card: 'Custom (문서 미연결)', node: { ...placed('gn_c2', 'custom', null, [line('instruction', 'T', doc('0013', 'T'), 'human'), line('report', 'TR', doc('0014', 'TR'), 'run')]), document: null } },
]
const PLAN = placed('gn_wp', 'document', 'WP', [line('plan', 'WP', doc('0003', 'WP'), 'human'), line('deploy', 'M', doc('0004', 'M'), 'run')])
const TEST_CARD = placed('gn_ts', 'document', 'TS', [line('instruction', 'TS', doc('0015', 'TS'), 'human'), line('scenario', 'TS', doc('0015', 'TS'), 'run'), line('report', 'TSR', doc('0016', 'TSR'), 'run')])

function graphOf(node: FlowGraphNode): FlowGraph {
  return { flowId: FLOW, nodes: [node], edges: [], ownership: 'unassigned', viewMode: 'draft', draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null } }
}

async function mountFrame(node: FlowGraphNode) {
  wrapper = mount(DesignerInspector, { attachTo: document.body, props: { selection: selectionOf(graphOf(node), node.id) }, global: { plugins: [i18n] } })
  await flushPromises()
  return wrapper
}

const rows = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-body"] [data-test="inspector-slot"]')
const windows = () => documentWindowList().map((win) => [win.docId, win.mode])

describe('a viewer: every card ✎ is off with the server\'s reason (same screen)', () => {
  it.each(CARDS)('$card: ✎ is disabled with the sentence as its tooltip; a stale press opens no editor', async ({ node }) => {
    grantProject(PROJECT_ID, 'role_viewer', REFUSED_DOCUMENT)
    const w = await mountFrame(node)
    const edits = w.findAll('[data-test="inspector-slot-edit"]')
    expect(edits.length).toBeGreaterThan(0)
    for (const edit of edits) {
      expect(edit.attributes('disabled')).toBeDefined()
      expect(edit.attributes('title')).toBe(VIEWER_SENTENCE)
      await edit.trigger('click')
      ;(edit.element as HTMLButtonElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    }
    expect(windows()).toEqual([])
    // Reading is untouched: the name still opens the document in the viewer.
    await rows(w)[0].get('[data-test="inspector-slot-open"]').trigger('click')
    expect(windows()).toEqual([[rows(w)[0].attributes('data-doc'), 'view']])
  })

  it.each(CARDS)('$card: a developer (nothing refused) still edits: ✎ enabled, opens the editor', async ({ node }) => {
    grantProject(PROJECT_ID, 'role_developer')
    const w = await mountFrame(node)
    const last = rows(w)[rows(w).length - 1]
    const edit = last.get('[data-test="inspector-slot-edit"]')
    expect(edit.attributes('disabled')).toBeUndefined()
    expect(edit.attributes('title')).toBe('편집')
    await edit.trigger('click')
    expect(windows()).toEqual([[last.attributes('data-doc'), 'edit']])
  })

  it('no answer from the server yet: nothing is turned off', async () => {
    const w = await mountFrame(CARDS[3].node)
    for (const edit of w.findAll('[data-test="inspector-slot-edit"]')) expect(edit.attributes('disabled')).toBeUndefined()
  })

  it('the reason follows the language (en · ja) and an answer that changes later (granted: on, taken away: off)', async () => {
    grantProject(PROJECT_ID, 'role_viewer', REFUSED_DOCUMENT)
    const w = await mountFrame(CARDS[3].node)
    const edit = () => w.findAll('[data-test="inspector-slot-edit"]')[0]
    expect(edit().attributes('title')).toBe(VIEWER_SENTENCE)
    i18n.global.locale.value = 'en'
    await flushPromises()
    expect(edit().attributes('title')).toMatch(/Viewer|View/i)
    i18n.global.locale.value = 'ja'
    await flushPromises()
    expect(edit().attributes('title')).toContain('開発者')
    i18n.global.locale.value = 'ko'
    grantProject(PROJECT_ID, 'role_developer')
    await flushPromises()
    expect(edit().attributes('disabled')).toBeUndefined()
    grantProject(PROJECT_ID, 'role_viewer', REFUSED_DOCUMENT)
    await flushPromises()
    expect(edit().attributes('disabled')).toBeDefined()
    expect(edit().attributes('title')).toBe(VIEWER_SENTENCE)
  })

  it('[작성] of a line without a document is disabled with the sentence and writes nothing', async () => {
    grantProject(PROJECT_ID, 'role_viewer', REFUSED_DOCUMENT)
    const node = placed('gn_t', 'document', 'T', [line('instruction', 'T', doc('0009', 'T'), 'human'), line('report', 'TR', null, 'run')])
    const w = await mountFrame(node)
    const write = w.get('[data-test="inspector-slot-action"][data-action="write"]')
    expect(write.attributes('disabled')).toBeDefined()
    expect(write.attributes('title')).toBe(VIEWER_SENTENCE)
    await write.trigger('click')
    expect(document.body.querySelector('[data-test="slot-write-window"]')).toBeNull()
  })

  it('[작성] is on for a developer', async () => {
    grantProject(PROJECT_ID, 'role_developer')
    const node = placed('gn_t', 'document', 'T', [line('instruction', 'T', doc('0009', 'T'), 'human'), line('report', 'TR', null, 'run')])
    const w = await mountFrame(node)
    expect(w.get('[data-test="inspector-slot-action"][data-action="write"]').attributes('disabled')).toBeUndefined()
  })

  it('the test card: its ✎ (instruction editor) is off with the sentence; a developer has it on', async () => {
    grantProject(PROJECT_ID, 'role_viewer', REFUSED_DOCUMENT)
    const w = await mountFrame(TEST_CARD)
    const brief = w.get('[data-test="test-card-brief"]')
    expect(brief.attributes('disabled')).toBeDefined()
    expect(brief.attributes('title')).toBe(VIEWER_SENTENCE)
    w.unmount()
    wrapper = null
    grantProject(PROJECT_ID, 'role_developer')
    const dev = await mountFrame(TEST_CARD)
    expect(dev.get('[data-test="test-card-brief"]').attributes('disabled')).toBeUndefined()
  })

  it('the work plan card: its plan window opens locked with the server\'s sentence; [작성] is off', async () => {
    grantProject(PROJECT_ID, 'role_viewer', REFUSED_DOCUMENT)
    const w = await mountFrame(PLAN)
    await rows(w)[0].get('[data-test="inspector-slot-open"]').trigger('click')
    const opened = workPlanEditWindowList()
    expect(opened.map((win) => win.docId)).toEqual([doc('0003', 'WP')])
    expect(opened[0].request.refusal?.()).toBe(VIEWER_SENTENCE)
    w.unmount()
    wrapper = null
    closeAllWorkPlanEditWindows()
    grantProject(PROJECT_ID, 'role_developer')
    const dev = await mountFrame(PLAN)
    await rows(dev)[0].get('[data-test="inspector-slot-open"]').trigger('click')
    expect(workPlanEditWindowList()[0].request.refusal?.() ?? null).toBeNull()
  })
})

describe('the card memos follow the document permissions (same screen)', () => {
  it('a viewer: the header memo button is off with the sentence; a developer has it on', async () => {
    grantProject(PROJECT_ID, 'role_viewer', REFUSED_DOCUMENT)
    const w = await mountFrame(CARDS[3].node)
    const button = w.get('[data-test="designer-inspector-memo"]')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.attributes('title')).toBe(VIEWER_SENTENCE)
    w.unmount()
    wrapper = null
    grantProject(PROJECT_ID, 'role_developer')
    const dev = await mountFrame(CARDS[3].node)
    expect(dev.get('[data-test="designer-inspector-memo"]').attributes('disabled')).toBeUndefined()
  })

  it('a new memo window of a viewer: fields and [저장] are off with the sentence', async () => {
    grantProject(PROJECT_ID, 'role_viewer', REFUSED_DOCUMENT)
    const node = CARDS[3].node
    const w = await mountFrame(node)
    const target = toInspectorTarget(selectionOf(graphOf(node), node.id))!
    openCardMemoWindow(cardMemoCardOf(target)!, null)
    await flushPromises()
    const save = document.body.querySelector<HTMLButtonElement>('[data-test="card-memo-save"]')
    expect(save).not.toBeNull()
    expect(save!.disabled).toBe(true)
    expect(save!.title).toBe(VIEWER_SENTENCE)
    expect(document.body.querySelector<HTMLInputElement>('[data-test="card-memo-title-input"]')!.disabled).toBe(true)
    expect(w.exists()).toBe(true)
  })
})

describe('InspectorSlotList (the shared line part)', () => {
  const LINES: InspectorSlotView[] = [
    { key: 'instruction', name: '지시서', id: 'doc-t', open: 'enabled', blank: true, actions: [] },
    { key: 'report', name: '레포트', id: null, empty: true, cardSlot: 'report', actions: [{ key: 'write', state: 'enabled' }] },
  ]
  const mountList = (props: Record<string, unknown>) => mount(InspectorSlotList, { props: { slots: LINES, editable: true, ...props }, global: { plugins: [i18n] } })

  it('edit refused: ✎ and [작성] are disabled with the reasons and emit nothing, however pressed', async () => {
    const w = mountList({ editRefusal: '고칠 수 없음', writeRefusal: '쓸 수 없음' })
    const edit = w.get('[data-test="inspector-slot-edit"]')
    expect([edit.attributes('disabled') !== undefined, edit.attributes('title')]).toEqual([true, '고칠 수 없음'])
    const write = w.get('[data-test="inspector-slot-action"][data-action="write"]')
    expect([write.attributes('disabled') !== undefined, write.attributes('title')]).toEqual([true, '쓸 수 없음'])
    ;(edit.element as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    ;(write.element as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(w.emitted('edit')).toBeUndefined()
    expect(w.emitted('action')).toBeUndefined()
  })

  it('a document without a body opens in the editor, or in the viewer when editing is refused', async () => {
    const free = mountList({})
    await free.get('[data-test="inspector-slot-name"]').trigger('click')
    expect(free.emitted('edit')).toHaveLength(1)
    expect(free.emitted('open')).toBeUndefined()
    const refused = mountList({ editRefusal: '고칠 수 없음' })
    await refused.get('[data-test="inspector-slot-name"]').trigger('click')
    expect(refused.emitted('edit')).toBeUndefined()
    expect(refused.emitted('open')).toHaveLength(1)
  })

  it('a reviewer (edit refused, approve on): ✎ is off, the approve button is untouched', async () => {
    const decide = vi.fn()
    const decisions = {
      failure: { value: null },
      row: (slot: string) => ({ visible: slot === 'instruction', authoritative: true, state: 'pending_review', action: 'approve' as const, disabled: false, reason: null }),
      decide,
    }
    const w = mountList({ editRefusal: VIEWER_SENTENCE, decisions })
    expect(w.get('[data-test="inspector-slot-edit"]').attributes('disabled')).toBeDefined()
    const approve = w.get('[data-test="inspector-document-decision"]')
    expect(approve.attributes('disabled')).toBeUndefined()
    await approve.trigger('click')
    expect(decide).toHaveBeenCalledTimes(1)
  })
})
