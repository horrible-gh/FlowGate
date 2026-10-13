/**
 * flowgate.v02.0053 T#8 — a card added with the Palette [+] (카드 추가) and placed is a Custom card (Graph `custom`
 * node) that works like a 조사 card, whichever Palette category it was added to (설계 · 업무 · 제어): placed, it is
 * never a gate, and the real Frame and registry show it as the 조사 card is shown — the same tabs, the 지시서 ·
 * 레포트 lines with the document icon and ✎, the same settings form, the Q&A input row and the attachments [+] —
 * titled with the name a person gave it. (The server side — no `rule_undecided/custom_node`, the run and its
 * result — is server/tests/test_fg_custom_card_0053.py.)
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import {
  createCustomPaletteItem,
  DEFAULT_PALETTE_ITEMS,
  PALETTE_CATEGORIES,
  paletteItemDefinition,
  paletteNodeSpec,
  type PaletteCategoryKey,
} from '@/features/designer/palette/paletteCatalog'
import { closeAllDocumentWindows } from '@/features/documentWindow'
import { prepareInspectorI18n, selectionOf } from './support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream } from './support/projectEventStream'

const nodeRead = vi.fn()
const choicesRead = vi.fn()

// Every other read of the Frame's features answers nothing: both cards meet the same offline server.
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const offline = () => Promise.reject(Object.assign(new Error('offline'), { request: {} }))
  return { ...actual, getRequest: vi.fn(offline), postRequest: vi.fn(offline), putRequest: vi.fn(offline), patchRequest: vi.fn(offline) }
})

vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    settingsNodeRead: (...args: unknown[]) => nodeRead(...args),
    settingsChoicesRead: (...args: unknown[]) => choicesRead(...args),
  }
})

const { toSettingsNode, toSettingsChoices } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const FLOW = 'flowgate.v02.0060'
const NAME = 'API 오류 코드 설계'

/**
 * The run card fields of the server (T#2 fields.py), nothing stored: the server keeps both cards as run cards.
 * flowgate.v02.0056 F2-1: the progress-after-review field is gone (O1 0005-TR §4-3), the 지시 AI is added (O2 §4-1).
 */
const RUN_FIELDS: readonly [string, unknown, readonly unknown[] | null][] = [
  ['instruction', 'use', ['use', 'omit']],
  ['instruction_ai', null, null],
  ['run_ai', null, null],
  ['restart', 0, [-1, 0, 1, 2, 3]],
  ['review_ai', null, null],
  ['review_count', 0, [-1, 0, 1, 2, 3]],
  ['time_limit', 60, [30, 45, 60, 90, 120, 180, 240]],
  ['review_basis', 'type', ['type', 'last']],
]

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  installProjectEventStream()
  nodeRead.mockReset()
  choicesRead.mockReset()
  nodeRead.mockImplementation(async (flowId: string, nodeKey: string) =>
    toSettingsNode({
      ok: true,
      flow_id: flowId,
      node_key: nodeKey,
      card_kind: 'run',
      locked: false,
      fields: Object.fromEntries(RUN_FIELDS.map(([name, def, choices]) => [name, { value: def, stored: false, default: def, choices }])),
    }),
  )
  choicesRead.mockImplementation(async () =>
    toSettingsChoices({ providers: [{ id: 'aip_opus', name: 'Claude Opus 5' }], execution_policy: { repeat_count_max: 3 } }),
  )
  closeAllDocumentWindows()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  closeAllDocumentWindows()
  removeProjectEventStream()
  document.body.innerHTML = ''
})

const doc = (key: string, type: string) => `${FLOW}.${key}-${type}`

function line(slot: string, docType: string, docId: string, author: 'human' | 'run'): FlowGraphCardDocument {
  return { slot, docType, author, docId, title: `카드 · ${slot}`, status: 'draft', docReviewStatus: null }
}

/** A card just placed, as the Canvas has it once its save answered (T#1 lines, the 지시서 as its document). */
function placed(
  key: string,
  spec: { nodeType: string; docType: string | null; paletteItemId: string; label: string; kind: string },
  lines: FlowGraphCardDocument[],
): FlowGraphNode {
  const first = lines[0]
  return {
    id: key,
    kind: spec.kind,
    title: spec.label,
    state: 'draft',
    docType: spec.docType,
    x: 0,
    y: 0,
    structureEditable: true,
    definition: { nodeType: spec.nodeType, docType: spec.docType, paletteItemId: spec.paletteItemId, label: spec.label, assignee: null, rootBound: false },
    document: { docId: first.docId!, typeCode: first.docType, seq: null, title: first.title, docReviewStatus: null, status: 'draft', supersedes: [] },
    cardDocuments: lines,
  }
}

const RESEARCH = placed(
  'gn_n',
  { nodeType: 'document', docType: 'N', paletteItemId: 'research', label: '조사', kind: 'research' },
  [line('instruction', 'N', doc('0007', 'N'), 'human'), line('report', 'NR', doc('0008', 'NR'), 'run')],
)

/** The node a card added to `category` with the Palette [+] becomes when it is dropped (the Designer's drop path). */
function customSpec(category: PaletteCategoryKey) {
  const made = createCustomPaletteItem({ category, name: NAME, icon: 'api', color: '#ec4899' }, DEFAULT_PALETTE_ITEMS)
  if (!made.ok) throw new Error(made.error)
  return paletteNodeSpec(paletteItemDefinition(made.item, made.item.label))
}

function customCard(category: PaletteCategoryKey): FlowGraphNode {
  return placed('gn_c', customSpec(category), [line('instruction', 'T', doc('0013', 'T'), 'human'), line('report', 'TR', doc('0014', 'TR'), 'run')])
}

async function mountFrame(node: FlowGraphNode) {
  const graph: FlowGraph = {
    flowId: FLOW,
    nodes: [node],
    edges: [],
    ownership: 'unassigned',
    viewMode: 'draft',
    draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
  }
  wrapper = mount(DesignerInspector, {
    attachTo: document.body,
    props: { selection: selectionOf(graph, node.id) },
    global: { plugins: [i18n, createPinia()] },
  })
  await flushPromises()
  return wrapper
}

const tabsOf = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]')

async function openTab(w: VueWrapper<any>, label: string) {
  await tabsOf(w).find((tab) => tab.text().startsWith(label))!.trigger('click')
  await flushPromises()
}

/** What the Inspector shows for a card: its tabs and what each of them holds. */
async function inspect(node: FlowGraphNode) {
  const w = await mountFrame(node)
  const title = w.get('[data-test="designer-inspector-title"]').text()
  const tabs = tabsOf(w).map((tab) => tab.text())
  const lines = w.findAll('[data-test="designer-inspector-body"] [data-test="inspector-slot"]').map((row) => [
    row.get('[data-test="inspector-slot-name"]').text(),
    row.get('[data-test="inspector-slot-open"]').attributes('title'),
    row.get('[data-test="inspector-slot-edit"]').attributes('title'),
  ])
  await openTab(w, 'Q&A')
  const ask = w.find('[data-test="qna-ask-input"]').exists()
  await openTab(w, '첨부')
  const add = w.find('[data-test="attachments-add"]').exists()
  await openTab(w, '설정')
  const settings = w.findAll('[data-test="inspector-control-row"]').map((row) => row.attributes('data-label'))
  const text = w.text()
  w.unmount()
  wrapper = null
  return { title, tabs, lines, ask, add, settings, text }
}

describe('a Custom card placed from the Palette [+] (flowgate.v02.0053 T#8)', () => {
  it.each(PALETTE_CATEGORIES)('added to %s: placed as a Custom document card, never a gate', (category) => {
    expect(customSpec(category)).toEqual({ nodeType: 'custom', docType: null, paletteItemId: 'custom.1', label: NAME, kind: 'custom.1' })
  })

  it.each(PALETTE_CATEGORIES)('added to %s: documents · settings · Q&A · attachments as the 조사 card shows them', async (category) => {
    const research = await inspect(RESEARCH)
    const custom = await inspect(customCard(category))
    expect(research.tabs).toEqual(['문서', 'Q&A', '첨부', '설정'])
    expect(research.lines).toEqual([
      ['지시서', '열기', '편집'],
      ['레포트', '열기', '편집'],
    ])
    expect(research.ask && research.add).toBe(true)
    expect(research.settings).toEqual(['지시서', '지시 AI', '실행 AI', '재시작', '검수 AI', '검수 횟수'])
    expect({ ...custom, title: null, text: null }).toEqual({ ...research, title: null, text: null })
    // both cards read their own card settings (the server keeps a Custom card as a run card)
    expect(nodeRead.mock.calls.map(([, key]) => key)).toEqual(expect.arrayContaining(['gn_n', 'gn_c']))
  })

  it('its Inspector title is the name a person gave it; no rule-undecided or gate wording', async () => {
    const custom = await inspect(customCard('control'))
    expect(custom.title).toBe(NAME)
    for (const word of ['사용자 정의 카드', '규칙', '확인 결과', '반려 ▾']) expect(custom.text).not.toContain(word)
  })
})
