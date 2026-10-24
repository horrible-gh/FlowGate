/**
 * flowgate.v02.0085 T0019 — the 확인 card is drawn the same in the Palette, on the Canvas and in the Inspector: the Palette
 * `confirm` card's user glyph and its name (확인 / Confirm / 確認) in every locale. Display only; the kind key stays `approval`.
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { flowNodeKindView } from '@/features/designer/center/canvas/flowNodeKinds'
import type { FlowGateNodeData } from '@/features/designer/center/canvas/vueFlowMapping'
import InspectorHeader from '@/features/designer/inspector/InspectorHeader.vue'
import { DEFAULT_PALETTE_ITEMS } from '@/features/designer/palette/paletteCatalog'
import { paletteIconPath } from '@/features/designer/palette/paletteIcons'
import { prepareInspectorI18n, SELECTIONS, targetOf } from './inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()
const LOCALES = [
  ['ko', ko, '확인'],
  ['en', en, 'Confirm'],
  ['ja', ja, '確認'],
] as const

const confirmItem = DEFAULT_PALETTE_ITEMS.find((i) => i.id === 'confirm')!
const wrappers: VueWrapper<any>[] = []
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount())
  i18n.global.locale.value = 'ko'
})

const mountCard = () => {
  const data: FlowGateNodeData = { kind: 'approval', docType: null, title: '', state: 'draft', selected: false }
  const w = mount(FlowNodeCard, { props: { id: 'gate:1', data }, global: { plugins: [i18n], stubs: { Handle: true } } })
  wrappers.push(w)
  return w
}

const mountHeader = () => {
  const target = { ...targetOf(SELECTIONS.v01), nodeKind: 'approval', docType: null }
  const w = mount(InspectorHeader, { props: { target }, global: { plugins: [i18n] } })
  wrappers.push(w)
  return w
}

describe('확인 card display is one in the Palette, the Canvas card and the Inspector (flowgate.v02.0085 T0019)', () => {
  it('the Canvas / Inspector kind takes the Palette confirm card’s user glyph, not the shield', () => {
    expect(confirmItem.nodeKind).toBe('approval')
    expect(confirmItem.icon).toBe('user')
    const view = flowNodeKindView('approval')
    expect(view.key).toBe('approval')
    expect(view.icon).toBe(paletteIconPath('user'))
    expect(view.icon).not.toBe(paletteIconPath('shield'))
  })

  it.each(LOCALES)('%s: the Palette, the Canvas card and the Inspector header all name it the same', (locale, messages, name) => {
    expect((messages as any).designer.palette.items.confirm).toBe(name)
    expect((messages as any).designer.center.node_kinds.approval).toBe(name)
    i18n.global.locale.value = locale
    const card = mountCard()
    const header = mountHeader()
    expect(card.get('[data-test="designer-canvas-node-kind"]').text()).toBe(name)
    expect(header.get('[data-test="designer-inspector-kind"]').text()).toBe(name)
    const glyph = paletteIconPath('user')
    expect(card.get('.flow-node-icon path').attributes('d')).toBe(glyph)
    expect(header.get('.inspector-header-icon path').attributes('d')).toBe(glyph)
    expect(card.get('[data-test="designer-canvas-node"]').attributes('data-kind')).toBe('approval')
  })

  it('the other card kinds keep their glyph and name', () => {
    expect(flowNodeKindView('task').labelKey).toBe('gui.designer.center.node_kinds.task')
    expect(flowNodeKindView('task').icon).toBe('M14 6l4 4l-8 8H6v-4l8-8z M12 8l4 4')
    expect(ko.designer.center.node_kinds.task).toBe('작업')
  })
})
