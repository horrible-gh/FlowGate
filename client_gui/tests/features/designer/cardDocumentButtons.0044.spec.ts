import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { cardDocumentButtons } from '@/features/designer/center/canvas/cardDocumentButtons'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'

// The shared i18n instance's locale may be left by another spec: accept the suffix in any app language.
const EMPTY_SUFFIX = /(\(비어 있음\)|\(empty\)|（空）)$/
const line = (slot: string, docId: string | null, docType = 'TS') => ({ slot, docId, docType, title: docId, author: null, status: 'draft', docReviewStatus: null, hasBody: false })
describe('0044 live document buttons', () => {
  it('F81-9: a shared TS document is one 시나리오 button, never an instruction button, regardless of slot order', () => {
    for (const lines of [[line('scenario', 'a'), line('instruction', 'a')], [line('instruction', 'a'), line('scenario', 'a')]]) {
      const docs = cardDocumentButtons({ cardDocuments: [...lines, line('report', 'b', 'TSR'), line('report', null)], document: null })
      expect(docs.map(d => [d.docId, d.ownerSlot, d.report, !!d.scenario])).toEqual([['a', 'scenario', false, true], ['b', 'report', true, false]])
    }
    expect(cardDocumentButtons({ cardDocuments: [], document: null })).toEqual([])
  })
  it('F81-9: an instruction line the server still sends for a TS card (no 시나리오 line) becomes the 시나리오 button', () => {
    const docs = cardDocumentButtons({ cardDocuments: [line('instruction', 'a'), line('report', 'b', 'TSR')], document: null })
    expect(docs.map(d => [d.docId, d.ownerSlot, !!d.scenario, d.report])).toEqual([['a', 'scenario', true, false], ['b', 'report', false, true]])
    // An omitted, empty instruction of a TS card is still its 시나리오 (the TS is the scenario, not an instruction).
    const omitted = cardDocumentButtons({ cardDocuments: [{ ...line('instruction', 'a'), instructionOmitted: true, hasBody: false, bodyState: 'readable' }, line('report', 'b', 'TSR')], document: null })
    expect(omitted.map(d => [d.ownerSlot, !!d.scenario])).toEqual([['scenario', true], ['report', false]])
  })
  it('gives a test card the Inspector\'s two buttons (시나리오 · 레포트) and opens the pressed one', async () => {
    const i18n = prepareInspectorI18n()
    const documents = cardDocumentButtons({ cardDocuments: [line('instruction', 'a'), line('scenario', 'a'), line('report', 'b', 'TSR')], document: null })
    const wrapper = mount(FlowNodeCard, { props: { id: 'n', data: { kind: 'test', title: 'Card', selected: false, state: null, documents } }, global: { plugins: [i18n], stubs: { Handle: true } } })
    const buttons = wrapper.findAll('[data-test="designer-canvas-node-document"]')
    expect(buttons).toHaveLength(2)
    expect(new Set(buttons.map(b => b.get('path').attributes('d'))).size).toBe(2)
    expect(buttons.map(b => b.attributes('data-scenario'))).toEqual(['true', 'false'])
    await buttons[0].trigger('click')
    expect(wrapper.emitted('open-document')).toEqual([[{ nodeId: 'n', docId: 'a', scenario: true }]])
    await buttons[1].trigger('click')
    expect(wrapper.emitted('open-document')?.[1]).toEqual([{ nodeId: 'n', docId: 'b' }])
    // A card without a scenario line (work, design ...) keeps its two buttons.
    expect(cardDocumentButtons({ cardDocuments: [line('instruction', 'a', 'T'), line('report', 'b', 'TR')], document: null })).toHaveLength(2)
    wrapper.unmount()
  })
  it('F81-9: an explicit 시나리오 line wins over a legacy TS instruction line of another document id', () => {
    for (const lines of [[line('scenario', 'a'), line('instruction', 'old')], [line('instruction', 'old'), line('scenario', 'a')]]) {
      const docs = cardDocumentButtons({ cardDocuments: [...lines, line('report', 'b', 'TSR')], document: null })
      expect(docs.map(d => [d.docId, d.ownerSlot, !!d.scenario, d.report])).toEqual([['a', 'scenario', true, false], ['b', 'report', false, true]])
    }
  })
  it('F81-9: an old card without lines whose document is a TS gets the 시나리오 button, not a document button', () => {
    const document = { docId: 'ts', typeCode: 'TS', title: 'Card · 테스트 · 지시서', seq: 1, docReviewStatus: null, status: 'draft', supersedes: [] }
    expect(cardDocumentButtons({ document, cardDocuments: [] }).map(d => [d.docId, d.ownerSlot, !!d.scenario, d.report])).toEqual([['ts', 'scenario', true, false]])
  })
  it('F81-9: the 시나리오 button never names a stored 「지시서」 role in its label', () => {
    const i18n = prepareInspectorI18n()
    const documents = cardDocumentButtons({ cardDocuments: [{ ...line('instruction', 'a'), title: 'Card · 테스트 · 지시서', hasBody: true }, line('report', 'b', 'TSR')], document: null })
    const wrapper = mount(FlowNodeCard, { props: { id: 'n', data: { kind: 'test', title: 'Card', selected: false, state: null, documents } }, global: { plugins: [i18n], stubs: { Handle: true } } })
    const scenario = wrapper.get('[data-scenario="true"]')
    expect(scenario.attributes('aria-label')).toContain('Card · 테스트 · ')
    expect(scenario.attributes('aria-label')).not.toMatch(/지시서|Instruction|指示書/)
    wrapper.unmount()
  })
  it('uses a free document only when there are no card slots; null live slots never revive an old document', () => {
    const document = { docId: 'free', typeCode: 'M', title: 'Memo', seq: 1, docReviewStatus: null, status: 'draft', supersedes: [] }
    expect(cardDocumentButtons({ document, cardDocuments: [] })[0].ownerSlot).toBeNull()
    expect(cardDocumentButtons({ document, cardDocuments: [line('report', null)] })).toEqual([])
  })
  it('opens exactly the pressed document, isolates pointer/keyboard events and follows replacement/deletion', async () => {
    const i18n = prepareInspectorI18n()
    const documents = cardDocumentButtons({ cardDocuments: [line('instruction', 'a'), line('report', 'b', 'TSR')], document: null })
    const wrapper = mount(FlowNodeCard, { props: { id: 'n', data: { kind: 'test', title: 'Card', selected: false, state: null, documents } }, global: { plugins: [i18n], stubs: { Handle: true } }, attachTo: document.body })
    const parentClick = vi.fn(); document.body.addEventListener('click', parentClick)
    const buttons = wrapper.findAll('[data-test="designer-canvas-node-document"]')
    expect(buttons).toHaveLength(2)
    expect(buttons[0].get('path').attributes('d')).not.toBe(buttons[1].get('path').attributes('d'))
    expect(buttons[1].attributes('aria-label')).toContain('TSR')
    await buttons[1].trigger('pointerdown'); await buttons[1].trigger('click')
    expect(wrapper.emitted('open-document')).toEqual([[{ nodeId: 'n', docId: 'b' }]])
    expect(parentClick).not.toHaveBeenCalled()
    await wrapper.setProps({ data: { ...wrapper.props('data'), documents: cardDocumentButtons({ cardDocuments: [line('report', 'new', 'TR')], document: null }) } })
    expect(wrapper.findAll('[data-test="designer-canvas-node-document"]')).toHaveLength(1)
    await wrapper.get('[data-doc-id="new"]').trigger('click')
    expect(wrapper.emitted('open-document')?.[1]).toEqual([{ nodeId: 'n', docId: 'new' }])
    await wrapper.setProps({ data: { ...wrapper.props('data'), documents: [] } })
    expect(wrapper.find('[data-test="designer-canvas-node-document"]').exists()).toBe(false)
    document.body.removeEventListener('click', parentClick); wrapper.unmount()
  })
  it('marks a document without a body as empty (never M / WP, never when has_body was not sent)', () => {
    const docs = cardDocumentButtons({ cardDocuments: [
      { ...line('instruction', 'blank', 'T'), hasBody: false },
      { ...line('report', 'full', 'TR'), hasBody: true },
      { ...line('plan', 'wp', 'WP'), hasBody: false },
      { ...line('memo', 'old', 'M'), hasBody: undefined },
      { slot: 'report', docId: 'unsent', docType: 'TR', title: 'unsent', author: null, status: 'draft', docReviewStatus: null },
    ], document: null })
    expect(docs.map(d => [d.docId, d.empty])).toEqual([['blank', true], ['full', false], ['wp', false], ['old', false], ['unsent', false]])
    const document = { docId: 'free', typeCode: 'M', title: 'Memo', seq: 1, docReviewStatus: null, status: 'draft', supersedes: [] }
    expect(cardDocumentButtons({ document, cardDocuments: [] })[0].empty).toBe(false)
  })
  it('draws an empty document faded with "(비어 있음)" in its name, and it still opens on a press', async () => {
    const i18n = prepareInspectorI18n()
    const documents = cardDocumentButtons({ cardDocuments: [{ ...line('instruction', 'a', 'T'), hasBody: false }, { ...line('report', 'b', 'TR'), hasBody: true }], document: null })
    const wrapper = mount(FlowNodeCard, { props: { id: 'n', data: { kind: 'test', title: 'Card', selected: false, state: null, documents } }, global: { plugins: [i18n], stubs: { Handle: true } } })
    const [empty, full] = wrapper.findAll('[data-test="designer-canvas-node-document"]')
    expect(empty.classes()).toContain('is-empty')
    expect(empty.attributes('data-empty')).toBe('true')
    expect(empty.attributes('disabled')).toBeUndefined()
    expect(empty.attributes('aria-label')).toMatch(EMPTY_SUFFIX)
    expect(empty.attributes('title')).toBe(empty.attributes('aria-label'))
    expect(full.classes()).not.toContain('is-empty')
    expect(full.attributes('data-empty')).toBe('false')
    expect(full.attributes('aria-label')).not.toMatch(EMPTY_SUFFIX)
    await empty.trigger('click')
    expect(wrapper.emitted('open-document')).toEqual([[{ nodeId: 'n', docId: 'a' }]])
    wrapper.unmount()
  })
})
