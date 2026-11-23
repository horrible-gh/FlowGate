/**
 * flowgate.v02test.0077 — the Flow and card numbers on screen: the Flow tab's `F77` before the name, the Canvas card
 * head's `#3` after the kind name (its hover `F74-3`), the server's `card_ref` carried from the Canvas read to the
 * Canvas card and the Inspector target, and what ID 복사 copies (the full id; the document id only without a number).
 * A card without a number (no `card_ref`, or null) shows none of them and nothing breaks.
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import FlowTabStrip from '@/features/designer/center/FlowTabStrip.vue'
import { toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { cardCopyId, flowLabel } from '@/features/designer/center/graph/cardIds'
import { toFlowGraph } from '@/features/designer/center/graph/serverFlowGraphSource'
import { toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { toCardRef, toFlowCanvas } from '@/features/project/flowCanvasApi'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()
const wrappers: VueWrapper<any>[] = []
afterEach(() => { wrappers.splice(0).forEach((w) => w.unmount()); i18n.global.locale.value = 'ko' })

const RAW_REF = { card_no: 3, local: '#3', short: 'F74-3', module_ref: 'v02test.F74-3', full: 'flowgate.v02test.F74-3' }
const REF = { cardNo: 3, local: '#3', short: 'F74-3', moduleRef: 'v02test.F74-3', full: 'flowgate.v02test.F74-3' }

describe('flowLabel / cardCopyId', () => {
  it('a group id -> F + its number without leading zeros; anything else -> null', () => {
    expect(flowLabel('flowgate.v02test.0077')).toBe('F77')
    expect(flowLabel('flowgate.v02test.0074')).toBe('F74')
    expect(flowLabel('flowgate.agent.0001')).toBe('F1')
    expect(flowLabel('flowgate.v02test.10000')).toBe('F10000')
    expect(flowLabel('a.b.c.0012')).toBe('F12')
    for (const id of ['a', 'f1', 'flowgate.v02test', 'flowgate.v02test.abc', 'flowgate..0077', 'flowgate.v02test.0000', '', null, undefined]) {
      expect(flowLabel(id)).toBeNull()
    }
  })

  it('ID 복사: the full card id; the document id only while the card has no number; else nothing', () => {
    expect(cardCopyId(REF, 'flowgate.v02test.0074.0003-T')).toBe('flowgate.v02test.F74-3')
    expect(cardCopyId(REF, null)).toBe('flowgate.v02test.F74-3')
    expect(cardCopyId(null, 'flowgate.v02test.0074.0003-T')).toBe('flowgate.v02test.0074.0003-T')
    expect(cardCopyId(undefined, null)).toBeNull()
  })
})

describe('card_ref from the Canvas read to the Canvas card and the Inspector', () => {
  it('toCardRef keeps a whole card_ref; null / malformed -> null', () => {
    expect(toCardRef(RAW_REF)).toEqual(REF)
    expect(toCardRef({ ...RAW_REF, module_ref: undefined })).toEqual({ ...REF, moduleRef: null })
    for (const bad of [null, undefined, 'F74-3', {}, { ...RAW_REF, card_no: 0 }, { ...RAW_REF, full: null }, { ...RAW_REF, short: 3 }]) {
      expect(toCardRef(bad)).toBeNull()
    }
  })

  const canvas = () => toFlowCanvas({
    flow_id: 'flowgate.v02test.0074', project_id: 'flowgate', ownership: { state: 'graph' }, flow_ref: { flow_no: 74, label: 'F74' },
    nodes: [
      { node_key: 'gn_a', kind: 'task', doc_type: 'T', title: 'Numbered', graph_node: { node_type: 'document' }, card_ref: RAW_REF },
      { node_key: 'gn_b', kind: 'task', doc_type: 'T', title: 'No number yet', graph_node: { node_type: 'document' }, card_ref: null },
      { node_key: 'gn_c', kind: 'task', doc_type: 'T', title: 'Older server', graph_node: { node_type: 'document' } },
    ],
    edges: [],
  })

  it('the Canvas node and the Graph node carry it as sent (null kept, absent stays absent)', () => {
    const read = canvas()
    expect(read.nodes.map((node) => node.cardRef)).toEqual([REF, null, undefined])
    expect('cardRef' in read.nodes[2]).toBe(false)
    const graph = toFlowGraph(read)
    expect(graph.nodes.map((node) => node.cardRef)).toEqual([REF, null, undefined])
    expect('cardRef' in graph.nodes[2]).toBe(false)
  })

  it('the Canvas card data gets #3 and F74-3 only for a numbered card', () => {
    const [a, b, c] = toVueFlowNodes(toFlowGraph(canvas()), null)
    expect(a.data.cardLocal).toBe('#3')
    expect(a.data.cardShort).toBe('F74-3')
    for (const node of [b, c]) {
      expect('cardLocal' in node.data).toBe(false)
      expect('cardShort' in node.data).toBe(false)
    }
  })

  it('the Inspector target gets cardRef only for a numbered card', () => {
    const graph = toFlowGraph(canvas())
    const target = (id: string) => toInspectorTarget({ projectId: 'flowgate', graph, node: graph.nodes.find((node) => node.id === id)! } as any)!
    expect(target('gn_a').cardRef).toEqual(REF)
    expect('cardRef' in target('gn_b')).toBe(false)
    expect('cardRef' in target('gn_c')).toBe(false)
  })
})

describe('the Canvas card head', () => {
  const render = (data: Record<string, unknown>) => {
    const w = mount(FlowNodeCard, {
      props: { id: 'gn_a', data: { kind: 'task', docType: 'T', title: '첨부파일 창', selected: false, state: null, ...data } },
      global: { plugins: [i18n], stubs: { Handle: true } },
    })
    wrappers.push(w)
    return w
  }

  it('#3 right after the kind name, its hover the short id; the title hover names it too', () => {
    const w = render({ cardLocal: '#3', cardShort: 'F74-3' })
    const head = w.get('.flow-node-head')
    const kids = [...head.element.children].map((el) => el.getAttribute('data-test'))
    expect(kids.indexOf('designer-canvas-node-no')).toBe(kids.indexOf('designer-canvas-node-kind') + 1)
    const no = w.get('[data-test="designer-canvas-node-no"]')
    expect(no.text()).toBe('#3')
    expect(no.attributes('title')).toBe('F74-3')
    expect(w.get('[data-test="designer-canvas-node-title"]').attributes('title')).toBe('F74-3 · 첨부파일 창')
  })

  it('a card without a number: no #, the title hover as before', () => {
    const w = render({})
    expect(w.find('[data-test="designer-canvas-node-no"]').exists()).toBe(false)
    expect(w.get('[data-test="designer-canvas-node-title"]').attributes('title')).toBe('첨부파일 창')
  })
})

describe('the Flow tab', () => {
  const tabs = [
    { flowId: 'flowgate.v02test.0074', name: '파일첨부 리뉴얼' },
    { flowId: 'flowgate.v02test.0076', name: '선' },
    { flowId: 'local-draft', name: '새 플로우' },
  ]
  const mountStrip = () => {
    const w = mount(FlowTabStrip, { props: { tabs, activeFlowId: 'flowgate.v02test.0074', chatStates: { 'flowgate.v02test.0076': true } }, global: { plugins: [i18n] }, attachTo: document.body })
    wrappers.push(w)
    return w
  }
  const button = (w: VueWrapper<any>, id: string) => w.get(`[data-test="designer-flow-tab"][data-flow-id="${id}"] [data-test="designer-flow-tab-button"]`)

  it('F number before the name (after the marks), the hover `F74 · name`; × stays', () => {
    const w = mountStrip()
    const first = button(w, 'flowgate.v02test.0074')
    expect(first.get('[data-test="designer-flow-tab-no"]').text()).toBe('F74')
    expect(first.text()).toMatch(/^F74\s*파일첨부 리뉴얼$/)
    expect(first.attributes('title')).toBe('F74 · 파일첨부 리뉴얼')
    const second = button(w, 'flowgate.v02test.0076')
    const kids = [...second.element.children].map((el) => el.getAttribute('data-test'))
    expect(kids).toEqual(['designer-flow-tab-chat', 'designer-flow-tab-no'])
    expect(second.get('[data-test="designer-flow-tab-no"]').text()).toBe('F76')
    expect(w.findAll('[data-test="designer-flow-tab"] .flow-tab-close')).toHaveLength(3)
  })

  it('a Flow id without a group number shows its name alone', () => {
    const w = mountStrip()
    const local = button(w, 'local-draft')
    expect(local.find('[data-test="designer-flow-tab-no"]').exists()).toBe(false)
    expect(local.text()).toBe('새 플로우')
    expect(local.attributes('title')).toBe('새 플로우')
  })
})
