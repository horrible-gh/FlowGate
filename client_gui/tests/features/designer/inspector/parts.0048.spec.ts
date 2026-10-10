/**
 * flowgate.v02.0048 T#1 — Inspector common display parts (D#1 §2.2 parts): status chip,
 * empty / loading / error state with an optional retry, and the reason line.
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import InspectorPaneState from '@/features/designer/inspector/parts/InspectorPaneState.vue'
import InspectorReason from '@/features/designer/inspector/parts/InspectorReason.vue'
import InspectorStatusChip from '@/features/designer/inspector/parts/InspectorStatusChip.vue'
import { prepareInspectorI18n } from './support/inspectorSupport'

const i18n = prepareInspectorI18n()

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

const global = { plugins: [i18n] }

describe('InspectorStatusChip', () => {
  it('shows the label with its tone (neutral by default)', () => {
    const w = mount(InspectorStatusChip, { props: { label: '승인됨', tone: 'success' }, global })
    expect(w.text()).toBe('승인됨')
    expect(w.get('[data-test="inspector-status-chip"]').attributes('data-tone')).toBe('success')
    expect(mount(InspectorStatusChip, { props: { label: 'x' }, global }).get('[data-test="inspector-status-chip"]').attributes('data-tone')).toBe('neutral')
  })
})

describe('InspectorPaneState', () => {
  it.each([
    ['loading', '불러오는 중…'],
    ['empty', '표시할 내용이 없습니다.'],
  ] as const)('%s shows the common wording', (state, text) => {
    const w = mount(InspectorPaneState, { props: { state }, global })
    expect(w.get('[data-test="inspector-pane-state"]').attributes('data-state')).toBe(state)
    expect(w.text()).toContain(text)
  })

  it('a caller message replaces the common empty wording', () => {
    expect(mount(InspectorPaneState, { props: { state: 'empty', message: '질문이 없습니다.' }, global }).text()).toBe('질문이 없습니다.')
  })

  it('error shows the common title, the caller message, and a retry only when asked', async () => {
    const w = mount(InspectorPaneState, { props: { state: 'error', message: '409 문서가 잠겨 있습니다.', retryable: true }, global })
    expect(w.get('[role="alert"]').text()).toContain('불러오지 못했습니다.')
    expect(w.text()).toContain('409 문서가 잠겨 있습니다.')
    await w.get('[data-test="inspector-pane-retry"]').trigger('click')
    expect(w.emitted('retry')).toHaveLength(1)
    expect(mount(InspectorPaneState, { props: { state: 'error' }, global }).find('[data-test="inspector-pane-retry"]').exists()).toBe(false)
  })

  it('follows the locale', () => {
    i18n.global.locale.value = 'ja'
    expect(mount(InspectorPaneState, { props: { state: 'error', retryable: true }, global }).text()).toContain('再試行')
  })
})

describe('InspectorReason', () => {
  it('shows the reason as a note with its tone', () => {
    const w = mount(InspectorReason, { props: { text: '실행 보기에서는 바꿀 수 없습니다.', tone: 'warning' } })
    const note = w.get('[data-test="inspector-reason"]')
    expect(note.attributes('role')).toBe('note')
    expect(note.attributes('data-tone')).toBe('warning')
    expect(note.text()).toBe('실행 보기에서는 바꿀 수 없습니다.')
  })
})
