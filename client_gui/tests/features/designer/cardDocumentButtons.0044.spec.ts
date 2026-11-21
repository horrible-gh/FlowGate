import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { cardDocumentButtons } from '@/features/designer/center/canvas/cardDocumentButtons'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'

// The shared i18n instance's locale may be left by another spec: accept the suffix in any app language.
const EMPTY_SUFFIX = /(\(비어 있음\)|\(empty\)|（空）)$/
const line = (slot: string, docId: string | null, docType = 'TS') => ({ slot, docId, docType, title: docId, author: null, status: 'draft', docReviewStatus: null, hasBody: false })
describe('0044 live document buttons', () => {
  it('deduplicates shared TS documents and prefers the instruction owner regardless of slot order', () => {
    const docs = cardDocumentButtons({ cardDocuments: [line('scenario', 'a'), line('instruction', 'a'), line('report', 'b', 'TSR'), line('report', null)], document: null })
    // flowgate.v02test.0041: the shared TS document keeps one instruction button and gets its own 시나리오 button right after it.
    expect(docs.map(d => [d.docId, d.ownerSlot, d.report, !!d.scenario])).toEqual([['a', 'instruction', false, false], ['a', 'scenario', false, true], ['b', 'report', true, false]])
    expect(cardDocumentButtons({ cardDocuments: [], document: null })).toEqual([])
  })
  it('gives a test card three buttons (지시서 · 시나리오 · 레포트) and opens the pressed one', async () => {
    const i18n = prepareInspectorI18n()
    const documents = cardDocumentButtons({ cardDocuments: [line('instruction', 'a'), line('scenario', 'a'), line('report', 'b', 'TSR')], document: null })
    const wrapper = mount(FlowNodeCard, { props: { id: 'n', data: { kind: 'test', title: 'Card', selected: false, state: null, documents } }, global: { plugins: [i18n], stubs: { Handle: true } } })
    const buttons = wrapper.findAll('[data-test="designer-canvas-node-document"]')
    expect(buttons).toHaveLength(3)
    expect(new Set(buttons.map(b => b.get('path').attributes('d'))).size).toBe(3)
    expect(buttons.map(b => b.attributes('data-scenario'))).toEqual(['false', 'true', 'false'])
    await buttons[1].trigger('click')
    expect(wrapper.emitted('open-document')).toEqual([[{ nodeId: 'n', docId: 'a', scenario: true }]])
    await buttons[0].trigger('click')
    expect(wrapper.emitted('open-document')?.[1]).toEqual([{ nodeId: 'n', docId: 'a' }])
    // A card without a scenario line (work, design ...) keeps its two buttons.
    expect(cardDocumentButtons({ cardDocuments: [line('instruction', 'a', 'T'), line('report', 'b', 'TR')], document: null })).toHaveLength(2)
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
