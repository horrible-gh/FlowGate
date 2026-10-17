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
import InspectorSlotList from '@/features/designer/inspector/parts/InspectorSlotList.vue'
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

// flowgate.v02.0053 T#5 (0053 CH): the line is `name · state · [document icon] · ✎` — the name opens the
// document (underlined on hover), no "not yet" text, no icon box.
// flowgate.v02.0056 F1 (0056 CH §5): the state is a short text badge drawn only when it says something (a
// decision waits, something runs, settled — dim); a neutral state draws nothing; a line without a document
// says why in the line (slotStateView).
describe('InspectorSlotRow', () => {
  it('a document line: the name, the state badge with its wording, the summary and the buttons slot', () => {
    const w = mount(InspectorSlotRow, {
      props: { name: '레포트', id: '0008.0007-TR', state: { label: '검수 대기', tone: 'warning' }, summary: '검수 2회', focus: true },
      slots: { default: () => h('button', { 'data-test': 'open' }, 'open') },
      global,
    })
    const line = w.get('[data-test="inspector-slot"]')
    expect(line.attributes('data-kind')).toBe('document')
    expect(line.attributes('data-empty')).toBe('false')
    expect(line.attributes('data-doc')).toBe('0008.0007-TR')
    expect(line.classes()).toContain('is-focus')
    const name = w.get('[data-test="inspector-slot-name"]')
    expect(name.text()).toBe('레포트')
    expect(name.attributes('title')).toBe('레포트')
    expect(name.element.tagName).toBe('SPAN')
    const badge = w.get('[data-test="inspector-slot-state"]')
    expect(badge.attributes('data-tone')).toBe('warning')
    expect(badge.attributes('data-kind')).toBe('badge')
    expect(badge.attributes('title')).toBe('검수 대기')
    expect(badge.text()).toBe('검수 대기')
    expect(badge.classes()).not.toContain('is-quiet')
    expect(w.find('[data-test="inspector-status-chip"]').exists()).toBe(false)
    expect(w.get('[data-test="inspector-slot-summary"]').text()).toBe('검수 2회')
    expect(w.get('[data-test="inspector-slot-buttons"] [data-test="open"]').exists()).toBe(true)
    expect(w.find('[data-test="inspector-slot-progress"]').exists()).toBe(false)
    // No icon box (📝 / ▤ / 🧪) before the name.
    expect(w.text()).not.toMatch(/📝|▤|🧪/)
  })

  it('openable: the name is a button that opens the document (an open event)', async () => {
    const w = mount(InspectorSlotRow, { props: { name: '지시서', id: 'doc-t', openable: true, state: { label: '수정됨', tone: 'info' } }, global })
    const name = w.get('[data-test="inspector-slot-name"]')
    expect(name.element.tagName).toBe('BUTTON')
    expect(name.classes()).toContain('is-link')
    await name.trigger('click')
    expect(w.emitted('open')).toHaveLength(1)
  })

  it('the badge tooltip adds the sub text and the title of the state; the badge shows the label', () => {
    const w = mount(InspectorSlotRow, { props: { name: '확인 결과', state: { label: '보류', tone: 'hold', sub: '2/4', title: '확인 완료' } }, global })
    const badge = w.get('[data-test="inspector-slot-state"]')
    expect(badge.attributes('title')).toBe('보류 · 2/4 · 확인 완료')
    expect(badge.text()).toBe('보류')
  })

  it('a settled state (success) is a dim badge; a neutral state and no state draw nothing', () => {
    const done = mount(InspectorSlotRow, { props: { name: '지시서', id: 'doc-t', state: { label: '승인됨', tone: 'success' } }, global })
    const badge = done.get('[data-test="inspector-slot-state"]')
    expect(badge.text()).toBe('승인됨')
    expect(badge.classes()).toContain('is-quiet')
    const writing = mount(InspectorSlotRow, { props: { name: '지시서', id: 'doc-t', state: { label: '작성 중', tone: 'neutral' } }, global })
    expect(writing.find('[data-test="inspector-slot-state"]').exists()).toBe(false)
    expect(writing.text()).toBe('지시서')
    const unknown = mount(InspectorSlotRow, { props: { name: '레포트', id: 'doc-tr' }, global })
    expect(unknown.find('[data-test="inspector-slot-state"]').exists()).toBe(false)
  })

  it('a line without a document: dashed, and why in plain words in the line (the caller words, else 문서 없음)', () => {
    const w = mount(InspectorSlotRow, { props: { name: '레포트', empty: true, emptyText: '실행 후' }, global })
    const line = w.get('[data-test="inspector-slot"]')
    expect(line.attributes('data-empty')).toBe('true')
    expect(line.classes()).toContain('is-empty')
    const why = w.get('[data-test="inspector-slot-state"]')
    expect(why.attributes('data-tone')).toBe('none')
    expect(why.attributes('data-kind')).toBe('empty')
    expect(why.attributes('title')).toBe('실행 후')
    expect(why.text()).toBe('실행 후')
    const plain = mount(InspectorSlotRow, { props: { name: '지시서', empty: true }, global })
    expect(plain.get('[data-test="inspector-slot-state"]').text()).toBe('문서 없음')
    expect(plain.text()).not.toContain('아직 없음')
  })

  it('a result line: the progress bar clamped to 0..100, the danger bar', () => {
    const w = mount(InspectorSlotRow, { props: { kind: 'result', name: '레포트', state: { label: '진행 중', tone: 'running' }, summary: 'automated 12/49', progress: 124 }, global })
    expect(w.get('[data-test="inspector-slot"]').attributes('data-kind')).toBe('result')
    expect(w.get('[data-test="inspector-slot-state"]').attributes('data-tone')).toBe('running')
    const bar = w.get('[data-test="inspector-slot-progress"]')
    expect(bar.attributes('aria-valuenow')).toBe('100')
    expect((bar.element.firstElementChild as HTMLElement).style.width).toBe('100%')
    const failed = mount(InspectorSlotRow, { props: { name: '레포트', empty: true, emptyText: '실패 · 제한 시간 초과', danger: true }, global })
    expect(failed.get('[data-test="inspector-slot"]').classes()).toContain('is-danger')
  })
})

// flowgate.v02.0052 T#4: on the 264px Inspector the "{nav} ↗" label is the part that gives way, so the
// open button after it stays in the line (the 확인 card's Git 패널 열기 ↗ used to push it out).
// flowgate.v02.0053 T#5: the nav button keeps its ↗ (it goes to another screen); the open button is the
// document icon.
describe('InspectorSlotList — nav before the document icon', () => {
  it('the nav button carries the shrinking class and comes before the document icon; both still emit', async () => {
    const line = { name: '확인 결과', kind: 'result' as const, nav: { label: 'Git 패널 열기' }, open: 'enabled' as const }
    const w = mount(InspectorSlotList, { props: { slots: [line] }, global })
    const buttons = w.get('[data-test="inspector-slot-buttons"]')
    const nav = buttons.get('[data-test="inspector-slot-nav"]')
    expect(nav.classes()).toContain('inspector-slot-nav')
    expect(nav.text()).toBe('Git 패널 열기 ↗')
    const order = Array.from(buttons.element.children)
    expect(order.indexOf(nav.element)).toBeLessThan(order.indexOf(buttons.get('[data-test="inspector-slot-open"]').element))
    await nav.trigger('click')
    await buttons.get('[data-test="inspector-slot-open"]').trigger('click')
    expect(w.emitted('nav')).toHaveLength(1)
    expect(w.emitted('open')).toHaveLength(1)
  })
})

// flowgate.v02.0056 F1 (0056 CH §2): a line that opens also has the history button (a clock, before the
// document icon) — the document's 검수·반려 이력 window; a line may leave it out or hand the entry count.
describe('InspectorSlotList — the history button (flowgate.v02.0056 F1)', () => {
  it('comes before the document icon on a line that opens, named 검수·반려 이력, and emits history with the line', async () => {
    const line = { name: '레포트', id: 'doc-tr', open: 'enabled' as const }
    const w = mount(InspectorSlotList, { props: { slots: [line] }, global })
    const buttons = w.get('[data-test="inspector-slot-buttons"]')
    const history = buttons.get('[data-test="inspector-slot-history"]')
    expect([history.attributes('data-icon'), history.attributes('title'), history.text()]).toEqual(['history', '검수·반려 이력', ''])
    expect(history.find('svg').exists()).toBe(true)
    const order = Array.from(buttons.element.children)
    expect(order.indexOf(history.element)).toBeLessThan(order.indexOf(buttons.get('[data-test="inspector-slot-open"]').element))
    await history.trigger('click')
    expect(w.emitted('history')).toEqual([[line]])
    expect(w.emitted('open')).toBeUndefined()
  })

  it('shows the entry count the line knows; left out when the line says so, when it has no id or does not open', () => {
    const counted = mount(InspectorSlotList, { props: { slots: [{ name: '레포트', id: 'doc-tr', open: 'enabled' as const, historyCount: 3 }] }, global })
    const history = counted.get('[data-test="inspector-slot-history"]')
    expect(history.get('[data-test="inspector-history-count"]').text()).toBe('3')
    expect(history.attributes('title')).toBe('검수·반려 이력 3건')
    for (const line of [
      { name: '레포트', id: 'doc-tr', open: 'enabled' as const, history: false },
      { name: '레포트', id: null, open: 'enabled' as const },
      { name: '레포트', id: 'doc-tr', open: 'disabled' as const },
      { name: '레포트', empty: true },
    ]) {
      expect(mount(InspectorSlotList, { props: { slots: [line] }, global }).find('[data-test="inspector-slot-history"]').exists()).toBe(false)
    }
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
    const w = mount(InspectorControlRow, { props: { label: '지시 AI', hint: '지시서를 쓰는 AI' }, slots: { default: () => h('select', { 'data-test': 'ctl' }) }, global })
    expect(w.get('[data-test="inspector-control-row"]').attributes('data-label')).toBe('지시 AI')
    expect(w.get('.inspector-control-label').attributes('title')).toBe('지시서를 쓰는 AI')
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
