/**
 * flowgate.v02.0051 — Inspector common display parts added or extended for the v7 screen (0051-R
 * §2.2): the seven-tone status chip, the `disabled` pane state, the slot line, the 🛡 review badge,
 * the origin badge, the block / control row / segment / lock line, and the run settings block.
 * Display only: each test checks what is shown for the given values and what leaves as an event.
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { h } from 'vue'
import InspectorBlock from '@/features/designer/inspector/parts/InspectorBlock.vue'
import InspectorControlRow from '@/features/designer/inspector/parts/InspectorControlRow.vue'
import InspectorLockNote from '@/features/designer/inspector/parts/InspectorLockNote.vue'
import InspectorOriginBadge from '@/features/designer/inspector/parts/InspectorOriginBadge.vue'
import InspectorPaneState from '@/features/designer/inspector/parts/InspectorPaneState.vue'
import InspectorReviewBadge from '@/features/designer/inspector/parts/InspectorReviewBadge.vue'
import InspectorRunSettings from '@/features/designer/inspector/parts/InspectorRunSettings.vue'
import InspectorSegment from '@/features/designer/inspector/parts/InspectorSegment.vue'
import InspectorSlotRow from '@/features/designer/inspector/parts/InspectorSlotRow.vue'
import InspectorStatusChip from '@/features/designer/inspector/parts/InspectorStatusChip.vue'
import { prepareInspectorI18n } from './support/inspectorSupport'

const i18n = prepareInspectorI18n()

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

const global = { plugins: [i18n] }

describe('InspectorStatusChip (v7 pill)', () => {
  it.each(['neutral', 'info', 'success', 'warning', 'danger', 'running', 'hold'] as const)('tone %s', (tone) => {
    const w = mount(InspectorStatusChip, { props: { label: '상태', tone }, global })
    expect(w.get('[data-test="inspector-status-chip"]').attributes('data-tone')).toBe(tone)
    expect(w.text()).toBe('상태')
  })

  it('shows the sub text after the label, the dot and the title', () => {
    const w = mount(InspectorStatusChip, { props: { label: '승인', tone: 'success', sub: '자동', dot: true, title: '자동 승인 — 전체 실행' }, global })
    expect(w.text()).toBe('승인· 자동')
    expect(w.get('[data-test="inspector-status-chip-sub"]').text()).toBe('· 자동')
    expect(w.get('[data-test="inspector-status-chip"]').classes()).toContain('inspector-status-chip--dot')
    expect(w.get('[data-test="inspector-status-chip"]').attributes('title')).toBe('자동 승인 — 전체 실행')
  })
})

describe('InspectorPaneState — disabled', () => {
  it('shows the common wording as an inactive note, or the caller message', () => {
    const w = mount(InspectorPaneState, { props: { state: 'disabled' }, global })
    expect(w.get('[data-test="inspector-pane-state"]').attributes('data-state')).toBe('disabled')
    expect(w.get('[role="note"]').attributes('aria-disabled')).toBe('true')
    expect(w.text()).toContain('아직 지원하지 않는 동작입니다.')
    expect(mount(InspectorPaneState, { props: { state: 'disabled', message: '메모는 아직 없습니다.' }, global }).text()).toContain('메모는 아직 없습니다.')
    i18n.global.locale.value = 'en'
    expect(mount(InspectorPaneState, { props: { state: 'disabled' }, global }).text()).toContain('Not supported yet.')
  })
})

describe('InspectorSlotRow', () => {
  it('a document line: name, state pill, summary and the buttons slot', () => {
    const w = mount(InspectorSlotRow, {
      props: { name: '레포트', id: '0008.0007-TR', state: { label: '검수 대기', tone: 'warning' }, summary: '검수 2회', focus: true },
      slots: { default: () => h('button', { 'data-test': 'open' }, '↗') },
      global,
    })
    const line = w.get('[data-test="inspector-slot"]')
    expect(line.attributes('data-kind')).toBe('document')
    expect(line.attributes('data-empty')).toBe('false')
    expect(line.classes()).toContain('is-focus')
    expect(w.get('[data-test="inspector-slot-name"]').text()).toBe('레포트')
    expect(w.get('[data-test="inspector-slot-name"]').attributes('title')).toBe('0008.0007-TR')
    expect(w.get('[data-test="inspector-status-chip"]').attributes('data-tone')).toBe('warning')
    expect(w.get('[data-test="inspector-slot-summary"]').text()).toBe('검수 2회')
    expect(w.get('[data-test="inspector-slot-buttons"] [data-test="open"]').exists()).toBe(true)
    expect(w.find('[data-test="inspector-slot-progress"]').exists()).toBe(false)
  })

  it('an empty line: dashed, the empty text (two words from the caller, else the common one), no pill', () => {
    const w = mount(InspectorSlotRow, { props: { name: '레포트', empty: true, emptyText: '실행 후' }, global })
    expect(w.get('[data-test="inspector-slot"]').attributes('data-empty')).toBe('true')
    expect(w.get('[data-test="inspector-slot"]').classes()).toContain('is-empty')
    expect(w.get('[data-test="inspector-slot-empty"]').text()).toBe('실행 후')
    expect(w.find('[data-test="inspector-status-chip"]').exists()).toBe(false)
    expect(mount(InspectorSlotRow, { props: { name: '지시서', empty: true }, global }).get('[data-test="inspector-slot-empty"]').text()).toBe('아직 없음')
  })

  it('a result line: ▤ icon, the progress bar clamped to 0..100, the danger bar', () => {
    const w = mount(InspectorSlotRow, { props: { kind: 'result', name: '결과 (TSR)', state: { label: '진행 중', tone: 'running' }, summary: 'automated 12/49', progress: 124 }, global })
    expect(w.get('[data-test="inspector-slot"]').attributes('data-kind')).toBe('result')
    expect(w.text()).toContain('▤')
    const bar = w.get('[data-test="inspector-slot-progress"]')
    expect(bar.attributes('aria-valuenow')).toBe('100')
    expect((bar.element.firstElementChild as HTMLElement).style.width).toBe('100%')
    const failed = mount(InspectorSlotRow, { props: { name: '레포트', empty: true, emptyText: '실패 · 제한 시간 초과', danger: true }, global })
    expect(failed.get('[data-test="inspector-slot"]').classes()).toContain('is-danger')
  })

  it('a form document (케이스 (TSC)) takes the 🧪 icon', () => {
    expect(mount(InspectorSlotRow, { props: { name: '케이스 (TSC)', form: true, empty: true }, global }).text()).toContain('🧪')
  })
})

describe('InspectorReviewBadge', () => {
  it.each([
    ['pass', 2, 0, 'AI 검수 2회 · 남은 지적 0'],
    ['warn', 1, null, 'AI 검수 1회 · 대응 필요'],
    ['run', 1, null, 'AI 검수 1회차 진행 중'],
    ['limit', 2, 1, '검수 횟수 소진 · 남은 지적 1'],
  ] as const)('%s: 🛡 n with the state color and the spelled-out title', (state, count, remain, title) => {
    const w = mount(InspectorReviewBadge, { props: { count, state, remain }, global })
    const badge = w.get('[data-test="inspector-review-badge"]')
    expect(badge.text()).toBe(`🛡 ${count}`)
    expect(badge.attributes('data-state')).toBe(state)
    expect(badge.attributes('title')).toBe(title)
  })

  it('a press leaves as click; disabled does not', async () => {
    const w = mount(InspectorReviewBadge, { props: { count: 2, state: 'pass', remain: 0 }, global })
    await w.get('button').trigger('click')
    expect(w.emitted('click')).toHaveLength(1)
    const off = mount(InspectorReviewBadge, { props: { count: 2, state: 'pass', remain: 0, disabled: true }, global })
    await off.get('button').trigger('click')
    expect(off.emitted('click')).toBeUndefined()
  })
})

describe('InspectorOriginBadge', () => {
  it('ai / wp with the default text and title, or the caller label', () => {
    const ai = mount(InspectorOriginBadge, { props: { origin: 'ai' }, global })
    expect(ai.text()).toBe('🤖 AI')
    expect(ai.get('[data-test="inspector-origin-badge"]').attributes('data-origin')).toBe('ai')
    expect(ai.get('[data-test="inspector-origin-badge"]').attributes('title')).toBe('AI 위임이 전개한 Node')
    const wp = mount(InspectorOriginBadge, { props: { origin: 'wp', label: '작성 사람', title: '사람 WP: 사람이 쓰고 사람이 전개' }, global })
    expect(wp.text()).toBe('작성 사람')
    expect(wp.get('[data-test="inspector-origin-badge"]').attributes('title')).toBe('사람 WP: 사람이 쓰고 사람이 전개')
  })
})

describe('InspectorBlock / InspectorControlRow / InspectorLockNote', () => {
  it('a block shows its title line (title · sub · head slot) and dims its body when locked', () => {
    const w = mount(InspectorBlock, {
      props: { title: '안전 한도', sub: '시스템 기본값', locked: true },
      slots: { head: () => h('button', { 'data-test': 'head' }, '고급'), default: () => h('p', { 'data-test': 'body' }, 'rows') },
      global,
    })
    expect(w.get('[data-test="inspector-block-title"]').text()).toBe('안전 한도')
    expect(w.get('[data-test="inspector-block-sub"]').text()).toBe('시스템 기본값')
    expect(w.get('[data-test="head"]').exists()).toBe(true)
    expect(w.get('[data-test="body"]').exists()).toBe(true)
    expect(w.get('[data-test="inspector-block"]').classes()).toContain('is-locked')
    expect(w.get('[data-test="inspector-block"]').attributes('aria-disabled')).toBe('true')
    expect(mount(InspectorBlock, { slots: { default: () => 'x' }, global }).find('.inspector-block-head').exists()).toBe(false)
  })

  it('a control row shows the label with its hint and the control', () => {
    const w = mount(InspectorControlRow, { props: { label: '검수 뒤 진행', hint: '실행 방식이 기본 정책' }, slots: { default: () => h('select', { 'data-test': 'ctl' }) }, global })
    expect(w.get('[data-test="inspector-control-row"]').attributes('data-label')).toBe('검수 뒤 진행')
    expect(w.get('.inspector-control-label').attributes('title')).toBe('실행 방식이 기본 정책')
    expect(w.get('[data-test="ctl"]').exists()).toBe(true)
  })

  it('the lock line says 🔒 실행 중, or the caller text', () => {
    expect(mount(InspectorLockNote, { global }).text()).toBe('🔒 실행 중')
    expect(mount(InspectorLockNote, { props: { text: '위임 진행 중' }, global }).text()).toBe('🔒 위임 진행 중')
  })
})

describe('InspectorSegment', () => {
  const options = [
    { value: 'use', label: '사용' },
    { value: 'skip', label: '생략' },
  ]

  it('is a radio group with the on option checked; a press on another leaves as update:modelValue', async () => {
    const w = mount(InspectorSegment, { props: { options, modelValue: 'use', ariaLabel: '지시서' }, global })
    expect(w.get('[role="radiogroup"]').attributes('aria-label')).toBe('지시서')
    const [use, skip] = w.findAll('[data-test="inspector-segment-option"]')
    expect(use.attributes('aria-checked')).toBe('true')
    expect(skip.attributes('aria-checked')).toBe('false')
    await use.trigger('click')
    expect(w.emitted('update:modelValue')).toBeUndefined()
    await skip.trigger('click')
    expect(w.emitted('update:modelValue')).toEqual([['skip']])
  })

  it('disabled keeps the look and refuses changes', async () => {
    const w = mount(InspectorSegment, { props: { options, modelValue: 'use', disabled: true }, global })
    for (const option of w.findAll('[data-test="inspector-segment-option"]')) expect(option.attributes('disabled')).toBeDefined()
    await w.findAll('[data-test="inspector-segment-option"]')[1].trigger('click')
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })
})

describe('InspectorRunSettings', () => {
  const fields = {
    run_ai: { value: 'p1', options: [{ value: 'p1', label: 'Claude Sonnet 5' }, { value: 'p2', label: 'Claude Opus 5' }] },
    review_ai: { value: 'p2', options: [{ value: 'p1', label: 'Claude Sonnet 5' }, { value: 'p2', label: 'Claude Opus 5' }] },
    review_count: { value: '2', options: [{ value: '1', label: '1회' }, { value: '2', label: '2회' }] },
  }

  it('shows the four base fields; a field not given is there, disabled and empty', () => {
    const w = mount(InspectorRunSettings, { props: { fields }, global })
    expect(w.get('[data-test="inspector-block-title"]').text()).toBe('실행 설정')
    expect(w.findAll('[data-test="inspector-control-row"]').map((row) => row.attributes('data-label'))).toEqual(['실행 AI', '재시작', '검수 AI', '검수 횟수'])
    expect((w.get('[data-test="inspector-run-settings-run_ai"]').element as HTMLSelectElement).value).toBe('p1')
    const restart = w.get('[data-test="inspector-run-settings-restart"]')
    expect(restart.attributes('disabled')).toBeDefined()
    expect(restart.text()).toContain('—')
    expect(w.find('[data-test="inspector-run-settings-advanced-panel"]').exists()).toBe(false)
  })

  it('a change leaves as update:field; 고급 opens the two advanced fields', async () => {
    const w = mount(InspectorRunSettings, { props: { fields, advancedOpen: false }, global })
    await w.get('[data-test="inspector-run-settings-review_count"]').setValue('1')
    expect(w.emitted('update:field')).toEqual([['review_count', '1']])
    await w.get('[data-test="inspector-run-settings-advanced"]').trigger('click')
    expect(w.emitted('update:advancedOpen')).toEqual([[true]])
    await w.setProps({ advancedOpen: true })
    expect(w.get('[data-test="inspector-run-settings"]').attributes('data-advanced')).toBe('open')
    expect(w.findAll('[data-test="inspector-run-settings-advanced-panel"] [data-test="inspector-control-row"]').map((row) => row.attributes('data-label'))).toEqual([
      '실행 제한 시간',
      '검수 기준',
    ])
  })

  it('locked disables every field and the 고급 button', () => {
    const w = mount(InspectorRunSettings, { props: { fields, locked: true }, global })
    for (const select of w.findAll('select')) expect(select.attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="inspector-run-settings-advanced"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="inspector-run-settings"]').classes()).toContain('is-locked')
  })
})
