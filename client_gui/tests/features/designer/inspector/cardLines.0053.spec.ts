/**
 * flowgate.v02.0053 T#5 — the documents tab lines of a card just placed (0053 CH; T#1 made its documents).
 * The real Frame and registry on a draft node as the server answers it after the save (its card lines and,
 * for the Inspector's document tabs, the first line a person writes as the node's document):
 *
 * - every card kind shows the lines of the table — 요구사항 · AI 위임 (only while on), 작업계획 · 전개,
 *   설계 (기본 · 프로토콜 · 로직 · DB) · 조사 · 작업 · Custom: 지시서 · 레포트, 테스트: 지시서 · 시나리오 · 레포트;
 * - each line is `name · [state] · [history] · [document icon] · ✎`: the name and the document icon (tooltip
 *   열기) open the document window, ✎ opens it straight in the editor — the report too, before any run; a
 *   draft draws no state badge (flowgate.v02.0056 F1), and the history button opens the 검수·반려 이력 window;
 * - no type code in a name, no "아직 없음", no ↗ and no "설명 대기" note on the tab;
 * - a card whose settings omit the instruction has no 지시서 line (v7); a card not saved yet reads nothing.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import { closeAllDocumentWindows, documentWindowList } from '@/features/documentWindow'
import { prepareInspectorI18n, selectionOf } from './support/inspectorSupport'

const settingsNodeRead = vi.hoisted(() => vi.fn())

// Every other read of the Frame's features answers nothing (the lines need none of them).
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const offline = () => Promise.reject(Object.assign(new Error('offline'), { request: {} }))
  return { ...actual, getRequest: vi.fn(offline), postRequest: vi.fn(offline), putRequest: vi.fn(offline), patchRequest: vi.fn(offline) }
})

vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return { ...actual, settingsNodeRead }
})

const i18n = prepareInspectorI18n()

const FLOW = 'flowgate.v02.0060'

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  settingsNodeRead.mockReset()
  settingsNodeRead.mockResolvedValue({ cardKind: 'run', locked: false, fields: { instruction: { value: 'use', stored: null, default: 'use', choices: ['use', 'omit'] } } })
  closeAllDocumentWindows()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  closeAllDocumentWindows()
  document.body.innerHTML = ''
})

/** A card line as the server answers it right after the card was placed (an empty draft). */
function line(slot: string, docType: string, docId: string, author: 'human' | 'run'): FlowGraphCardDocument {
  return { slot, docType, author, docId, title: `카드 · ${slot}`, status: 'draft', docReviewStatus: null }
}

/** A draft node just placed, as the shown Graph has it once the save answered its key and lines. */
function placed(key: string, nodeType: string, docType: string | null, lines: FlowGraphCardDocument[]): FlowGraphNode {
  const first = lines.find((candidate) => candidate.author === 'human') ?? lines[0]
  return {
    id: key,
    kind: 'task',
    title: '새 카드',
    state: 'draft',
    docType,
    x: 0,
    y: 0,
    structureEditable: true,
    definition: { nodeType, docType, paletteItemId: null, label: '새 카드', assignee: null, rootBound: false },
    document: first
      ? { docId: first.docId!, typeCode: first.docType, seq: null, title: first.title, docReviewStatus: null, status: 'draft', supersedes: [] }
      : null,
    cardDocuments: lines,
  }
}

const doc = (key: string, type: string) => `${FLOW}.${key}-${type}`

interface PlacedCard {
  readonly card: string
  readonly section: string
  readonly node: FlowGraphNode
  readonly names: readonly string[]
}

/** Every kind of card the palette places, with the lines T#1 makes for it. */
const CARDS: readonly PlacedCard[] = [
  { card: '요구사항', section: 'requirementCard', node: placed('gn_r', 'document', 'R', [line('requirement', 'R', doc('0001', 'R'), 'human')]), names: ['요구사항'] },
  {
    card: '요구사항 (AI 위임 켬)',
    section: 'requirementCard',
    node: placed('gn_r2', 'document', 'R', [line('requirement', 'R', doc('0001', 'R'), 'human'), line('delegation', 'M', doc('0002', 'M'), 'run')]),
    names: ['요구사항', 'AI 위임'],
  },
  { card: '작업계획', section: 'workPlan', node: placed('gn_wp', 'document', 'WP', [line('plan', 'WP', doc('0003', 'WP'), 'human'), line('deploy', 'M', doc('0004', 'M'), 'run')]), names: ['작업계획', '전개'] },
  ...['D', 'P', 'L', 'DB'].map((type) => ({
    card: `설계 ${type}`,
    section: 'designCard',
    node: placed(`gn_${type.toLowerCase()}`, 'document', type, [line('instruction', 'DS', doc('0005', 'DS'), 'human'), line('report', type, doc('0006', type), 'run')]),
    names: ['지시서', '레포트'],
  })),
  { card: '조사', section: 'researchCard', node: placed('gn_n', 'document', 'N', [line('instruction', 'N', doc('0007', 'N'), 'human'), line('report', 'NR', doc('0008', 'NR'), 'run')]), names: ['지시서', '레포트'] },
  { card: '작업', section: 'taskCard', node: placed('gn_t', 'document', 'T', [line('instruction', 'T', doc('0009', 'T'), 'human'), line('report', 'TR', doc('0010', 'TR'), 'run')]), names: ['지시서', '레포트'] },
  { card: '반영', section: 'taskCard', node: placed('gn_t2', 'document', 'T2', [line('instruction', 'T2', doc('0011', 'T2'), 'human'), line('report', 'TR2', doc('0012', 'TR2'), 'run')]), names: ['지시서', '레포트'] },
  // A Custom card holds T / TR documents (T#1), so with its 지시서 bound it is drawn by the 작업 card part; without
  // a bound document the gate part draws the same two lines (Custom without a document, below).
  { card: 'Custom', section: 'taskCard', node: placed('gn_c', 'custom', null, [line('instruction', 'T', doc('0013', 'T'), 'human'), line('report', 'TR', doc('0014', 'TR'), 'run')]), names: ['지시서', '레포트'] },
  {
    card: 'Custom (문서 미연결)',
    section: 'gateCard',
    node: { ...placed('gn_c2', 'custom', null, [line('instruction', 'T', doc('0013', 'T'), 'human'), line('report', 'TR', doc('0014', 'TR'), 'run')]), document: null },
    names: ['지시서', '레포트'],
  },
  {
    card: '테스트',
    section: 'testSpec',
    node: placed('gn_ts', 'document', 'TS', [line('instruction', 'TS', doc('0015', 'TS'), 'human'), line('scenario', 'TS', doc('0015', 'TS'), 'run'), line('report', 'TSR', doc('0016', 'TSR'), 'run')]),
    names: ['지시서', '시나리오', '레포트'],
  },
]

function graphOf(node: FlowGraphNode): FlowGraph {
  return { flowId: FLOW, nodes: [node], edges: [], ownership: 'unassigned', viewMode: 'draft', draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null } }
}

async function mountFrame(node: FlowGraphNode) {
  wrapper = mount(DesignerInspector, {
    attachTo: document.body,
    props: { selection: selectionOf(graphOf(node), node.id) },
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}

const lines = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-body"] [data-test="inspector-slot"]')
const windows = () => documentWindowList().map((win) => [win.docId, win.mode])

/** Type codes a line name must not carry (0053 CH: no WP · TS · TSC · TSR · D · L · P · DB …). */
const TYPE_CODE = /\b(?:R|B|M|WP|DS|D|P|L|DB|N|NR|T|TR|T2|TR2|TS|TSC|TSR|AC)\b/

describe('the documents tab of a card just placed (flowgate.v02.0053 T#5)', () => {
  it.each(CARDS)('$card: the lines of the table, each without a badge while drafted, with the history button, the document icon and ✎', async ({ node, section, names }) => {
    const w = await mountFrame(node)
    expect(w.findAll('[data-test="designer-inspector-section"]').map((part) => part.attributes('data-key'))).toEqual([section])
    const shown = lines(w)
    expect(shown.map((row) => row.get('[data-test="inspector-slot-name"]').text())).toEqual([...names])
    expect(shown.map((row) => row.attributes('data-doc'))).toEqual(node.cardDocuments!.map((own) => own.docId))
    for (const row of shown) {
      expect(row.attributes('data-empty')).toBe('false')
      // flowgate.v02.0056 F1: a document being written asks nothing of the operator — no badge.
      expect(row.find('[data-test="inspector-slot-state"]').exists()).toBe(false)
      const history = row.get('[data-test="inspector-slot-history"]')
      expect([history.attributes('data-icon'), history.attributes('title'), history.text()]).toEqual(['history', '검수·반려 이력', ''])
      const name = row.get('[data-test="inspector-slot-name"]')
      expect(name.element.tagName).toBe('BUTTON')
      expect(TYPE_CODE.test(name.text())).toBe(false)
      const open = row.get('[data-test="inspector-slot-open"]')
      expect([open.attributes('data-icon'), open.attributes('title'), open.text()]).toEqual(['document', '열기', ''])
      expect(row.get('[data-test="inspector-slot-edit"]').attributes('title')).toBe('편집')
    }
    const body = w.get('[data-test="designer-inspector-body"]').text()
    for (const word of ['아직 없음', '↗', '설명 대기', '설계 카드는 아직 비워 두었습니다', '작성 필요', '＋']) expect(body).not.toContain(word)
  })

  it.each(CARDS)('$card: the name and the document icon open each line in its window; ✎ straight in the editor', async ({ node }) => {
    const w = await mountFrame(node)
    const shown = lines(w)
    const first = shown[0]
    const last = shown[shown.length - 1]
    await first.get('[data-test="inspector-slot-name"]').trigger('click')
    expect(windows()).toEqual([[first.attributes('data-doc'), 'view']])
    closeAllDocumentWindows()
    // The report (a run fills it) opens before any run too — an empty document.
    await last.get('[data-test="inspector-slot-open"]').trigger('click')
    expect(windows()).toEqual([[last.attributes('data-doc'), 'view']])
    closeAllDocumentWindows()
    await last.get('[data-test="inspector-slot-edit"]').trigger('click')
    expect(windows()).toEqual([[last.attributes('data-doc'), 'edit']])
  })

  it('a requirement card shows AI 위임 only while the server keeps its line (the setting is on)', async () => {
    const off = await mountFrame(CARDS[0].node)
    expect(lines(off).map((row) => row.get('[data-test="inspector-slot-name"]').text())).toEqual(['요구사항'])
    off.unmount()
    wrapper = null
    const on = await mountFrame(CARDS[1].node)
    const delegation = lines(on)[1]
    expect(delegation.attributes('data-kind')).toBe('result')
    expect(delegation.attributes('data-doc')).toBe(doc('0002', 'M'))
  })

  it('a test card without a bound document still shows its three lines (no empty tab)', async () => {
    const node = { ...CARDS[CARDS.length - 1].node, document: null }
    const w = await mountFrame(node)
    expect(lines(w).map((row) => row.attributes('data-doc'))).toEqual([doc('0015', 'TS'), doc('0015', 'TS'), doc('0016', 'TSR')])
    expect(w.find('[data-test="designer-inspector-tab-state"]').exists()).toBe(false)
    expect(w.find('[data-test="test-spec-details"]').exists()).toBe(false)
  })

  it('a line whose document is gone keeps its place saying so in the line, with nothing to open', async () => {
    const node = placed('gn_t', 'document', 'T', [line('instruction', 'T', doc('0009', 'T'), 'human'), { ...line('report', 'TR', doc('0010', 'TR'), 'run'), docId: null }])
    const w = await mountFrame(node)
    const report = lines(w)[1]
    expect(report.attributes('data-empty')).toBe('true')
    expect(report.get('[data-test="inspector-slot-state"]').text()).toBe('문서 없음')
    expect(report.find('[data-test="inspector-slot-open"]').exists()).toBe(false)
    expect(report.find('[data-test="inspector-slot-history"]').exists()).toBe(false)
    expect(report.get('[data-test="inspector-slot-name"]').element.tagName).toBe('SPAN')
  })

  it('the lines follow the documents’ states: a report waiting for review is the line to look at', async () => {
    const node = placed('gn_t', 'document', 'T', [
      { ...line('instruction', 'T', doc('0009', 'T'), 'human'), status: 'closed', docReviewStatus: 'approved' },
      { ...line('report', 'TR', doc('0010', 'TR'), 'run'), status: 'open', docReviewStatus: 'pending_review' },
    ])
    const w = await mountFrame(node)
    const [instruction, report] = lines(w)
    expect(instruction.get('[data-test="inspector-slot-state"]').text()).toBe('승인됨')
    expect(instruction.get('[data-test="inspector-slot-state"]').classes()).toContain('is-quiet')
    expect(report.get('[data-test="inspector-slot-state"]').attributes('data-tone')).toBe('warning')
    expect(report.get('[data-test="inspector-slot-state"]').text()).toBe('검수 대기')
    expect(report.classes()).toContain('is-focus')
  })
})

describe('the 지시서 line follows the card settings (flowgate.v02.0053 T#5, v7)', () => {
  const omitting = { cardKind: 'run', locked: false, fields: { instruction: { value: 'omit', stored: 'omit', default: 'use', choices: ['use', 'omit'] } } }

  it.each(CARDS.filter((card) => card.names[0] === '지시서'))('$card: instruction omitted -> no 지시서 line', async ({ node, names }) => {
    settingsNodeRead.mockResolvedValue(omitting)
    const w = await mountFrame(node)
    expect(settingsNodeRead).toHaveBeenCalledWith(FLOW, node.id)
    expect(lines(w).map((row) => row.get('[data-test="inspector-slot-name"]').text())).toEqual(names.slice(1))
  })

  it('instruction used, a failed read or a card not saved yet (`tmp_`): the 지시서 line stays', async () => {
    const task = CARDS.find((card) => card.card === '작업')!.node
    let w = await mountFrame(task)
    expect(lines(w)).toHaveLength(2)
    w.unmount()
    settingsNodeRead.mockRejectedValue(new Error('offline'))
    w = await mountFrame(task)
    expect(lines(w)).toHaveLength(2)
    w.unmount()
    settingsNodeRead.mockClear()
    settingsNodeRead.mockResolvedValue(omitting)
    w = await mountFrame({ ...task, id: 'tmp_abc' })
    expect(settingsNodeRead).not.toHaveBeenCalled()
    expect(lines(w)).toHaveLength(2)
  })

  it('a requirement or work plan card reads no card settings for its lines', async () => {
    ;(await mountFrame(CARDS[0].node)).unmount()
    await mountFrame(CARDS[2].node)
    expect(settingsNodeRead).not.toHaveBeenCalled()
  })

  it('another card selected: the lines follow its settings, a late answer of the previous card is dropped', async () => {
    let answer: (value: unknown) => void = () => undefined
    settingsNodeRead.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)))
    const task = CARDS.find((card) => card.card === '작업')!.node
    const research = CARDS.find((card) => card.card === '조사')!.node
    const w = await mountFrame(task)
    await w.setProps({ selection: selectionOf(graphOf(research), research.id) })
    await flushPromises()
    answer(omitting)
    await flushPromises()
    expect(lines(w).map((row) => row.get('[data-test="inspector-slot-name"]').text())).toEqual(['지시서', '레포트'])
  })
})
