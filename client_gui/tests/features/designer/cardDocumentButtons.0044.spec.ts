import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { cardDocumentButtons } from '@/features/designer/center/canvas/cardDocumentButtons'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'

const line = (slot: string, docId: string | null, docType = 'TS') => ({ slot, docId, docType, title: docId, author: null, status: 'draft', docReviewStatus: null, hasBody: false })
describe('0044 live document buttons', () => {
  it('deduplicates shared TS documents and prefers the instruction owner regardless of slot order', () => {
    const docs = cardDocumentButtons({ cardDocuments: [line('scenario', 'a'), line('instruction', 'a'), line('report', 'b', 'TSR'), line('report', null)], document: null })
    expect(docs.map(d => [d.docId, d.ownerSlot, d.report])).toEqual([['a', 'instruction', false], ['b', 'report', true]])
    expect(cardDocumentButtons({ cardDocuments: [], document: null })).toEqual([])
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
})
