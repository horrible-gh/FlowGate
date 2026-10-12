/**
 * flowgate.v02.0053 T#3 — the slot line's two entry points into the document window: ↗ (open, tooltip 열기
 * again — no new-tab / sign-in text) and, with `editable`, ✎ (open straight in the editor) on every line whose
 * ↗ is usable. Display only: the part emits `open` / `edit` with the line.
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import InspectorSlotList, { type InspectorSlotView } from '@/features/designer/inspector/parts/InspectorSlotList.vue'
import { prepareInspectorI18n } from './support/inspectorSupport'

const i18n = prepareInspectorI18n()

const LINES: InspectorSlotView[] = [
  { key: 'instruction', name: '지시서', id: 'doc-t', open: 'enabled' },
  { key: 'report', name: '레포트', id: 'doc-tr', open: 'disabled', openReason: '아직 연결되지 않음' },
  { key: 'result', kind: 'result', name: '결과', empty: true },
]

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

function mountList(editable: boolean) {
  return mount(InspectorSlotList, { props: { slots: LINES, editable }, global: { plugins: [i18n] } })
}

describe('InspectorSlotList ↗ / ✎ (flowgate.v02.0053 T#3)', () => {
  it('↗ says 열기; nothing on the line talks about a new tab, the v0.1 screen or signing in', () => {
    const w = mountList(true)
    const opens = w.findAll('[data-test="inspector-slot-open"]')
    expect(opens.map((button) => button.attributes('title'))).toEqual(['열기', '아직 연결되지 않음'])
    expect(w.html()).not.toMatch(/새 탭|v0\.1|로그인/)
  })

  it('editable: ✎ only on the line whose ↗ is usable; it emits edit with that line', async () => {
    const w = mountList(true)
    const edits = w.findAll('[data-test="inspector-slot-edit"]')
    expect(edits).toHaveLength(1)
    expect(edits[0].attributes('title')).toBe('편집')
    expect(edits[0].text()).toBe('✎')
    await edits[0].trigger('click')
    expect(w.emitted('edit')).toEqual([[LINES[0]]])
    await w.find('[data-test="inspector-slot-open"]').trigger('click')
    expect(w.emitted('open')).toEqual([[LINES[0]]])
  })

  it('not editable (a read-only Flow): no ✎, ↗ stays', () => {
    const w = mountList(false)
    expect(w.findAll('[data-test="inspector-slot-edit"]')).toHaveLength(0)
    expect(w.findAll('[data-test="inspector-slot-open"]')).toHaveLength(2)
  })
})
