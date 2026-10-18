/**
 * flowgate.v02.0064 T#4 (0062 D0005 §3.2 / §6 세트 2, 0064 P0006 §6.5), moved by flowgate.v02.0070 T#1 (B1): a card's own
 * title is edited in the Inspector header's title area, never on the small Canvas card.
 *
 * - FlowNodeCard: the title is shown only — no input, ✓ / ×, ✎ or length note; a click on it is the card's (the
 *   selection), and [승인] / the integration chip stay as they are;
 * - InspectorHeader: a title that may be edited now (`canEditTitle`, with the host's `saveTitle`) turns into an input in
 *   place on a click or on Enter / F2; a title that repeats the kind name keeps a small ✎ entry. Enter saves the raw text
 *   for the Flow and card the edit was opened on, Esc or a press outside cancels, Enter / Esc while an IME composes only
 *   end the composition; the screen refuses an empty / over-long title before sending; a server refusal keeps the input
 *   and names it, `node_title_locked` and a card that starts running close the edit with the lock note; nothing is
 *   shown before the server answered; another card selected while a save is answered closes the edit and the late
 *   answer never reaches the newly selected card's header;
 * - on the real Canvas + Inspector (DesignerCenter, DesignerInspector, the session): a title click on a card selects it
 *   and opens nothing there; the header saves through the session (the card and the header show the answer, the draft
 *   and its history untouched); a late answer after another card was selected goes onto its own card only; the run
 *   read's running lock closes the header edit; the Canvas key handler still ignores keys from any text field;
 * - the Center column carries the save to the session's saveNodeTitle with the Flow and card it is given.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import type { FlowGateNodeData } from '@/features/designer/center/canvas/vueFlowMapping'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph, FlowGraphNode, FlowGraphSelection, FlowGraphTitleState } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunInfo, FlowRunNodeInfo } from '@/features/designer/center/graph/flowGraphSource'
import type { NodeTitleSaveOutcome } from '@/features/designer/center/graph/nodeTitle'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import InspectorHeader from '@/features/designer/inspector/InspectorHeader.vue'
import { INSPECTOR_HOST } from '@/features/designer/inspector/inspectorHost'
import { toInspectorTarget, type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import DesignerCenterColumn from '@/features/designer/shell/DesignerCenterColumn.vue'
import { fixtureRegistry } from './inspector/support/inspectorSupport'

registerAppMessages(i18n)

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
})

const ts = (title: string | null, version: number): FlowGraphTitleState => ({ title, titleSource: title === null ? null : 'human', version })

// --- FlowNodeCard ---------------------------------------------------------------------------------------------

describe('FlowNodeCard — the title is shown only (0070 T#1)', () => {
  let wrapper: VueWrapper<any> | null = null
  const outer = { click: vi.fn(), pointerdown: vi.fn() }

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    for (const spy of Object.values(outer)) spy.mockReset()
  })

  const data: FlowGateNodeData = { kind: 'task', docType: 'T', title: '로그인 개선', state: 'draft', selected: false }

  /** The card inside a host that records what reaches it (the Vue Flow node wrapper's place). */
  function mountCard(cardData: FlowGateNodeData) {
    const Host = defineComponent({
      props: { data: { type: Object, required: true } },
      setup: (props) => () =>
        h('div', { 'data-test': 'host', onClick: outer.click, onPointerdown: outer.pointerdown }, [
          h(FlowNodeCard, { id: 'n_1', data: props.data as FlowGateNodeData }),
        ]),
    })
    i18n.global.locale.value = 'ko'
    wrapper = mount(Host, { props: { data: cardData }, attachTo: document.body, global: { plugins: [i18n], stubs: { Handle: true } } })
    return wrapper
  }

  const find = (w: VueWrapper<any>, test: string) => w.find(`[data-test="designer-canvas-node-${test}"]`)

  it('shows the title with its tooltip; no input, ✓ / ×, ✎ or length note; a click on it is the card’s', async () => {
    const w = mountCard(data)
    const title = find(w, 'title')
    expect(title.text()).toBe('로그인 개선')
    expect(title.attributes('title')).toBe('로그인 개선')
    expect(title.classes()).not.toContain('is-editable')
    for (const part of ['title-editor', 'title-input', 'title-save', 'title-cancel', 'title-edit', 'title-note']) {
      expect(find(w, part).exists(), part).toBe(false)
    }
    expect(w.findAll('input, textarea, [contenteditable]')).toHaveLength(0)
    await title.trigger('pointerdown')
    await title.trigger('click')
    expect(outer.pointerdown).toHaveBeenCalledTimes(1)
    expect(outer.click).toHaveBeenCalledTimes(1)
    expect(find(w, 'title-input').exists()).toBe(false)
  })

  it('an editable title state of the old card data opens nothing either (the card data has no edit fields)', async () => {
    const w = mountCard({ ...data, titleEditable: true } as FlowGateNodeData)
    await find(w, 'title').trigger('click')
    expect(find(w, 'title-input').exists()).toBe(false)
    expect(find(w, 'title-edit').exists()).toBe(false)
  })

  it('a title that repeats the kind name is not shown, and no ✎ takes its place', () => {
    const w = mountCard({ ...data, title: '작업' })
    expect(find(w, 'title').exists()).toBe(false)
    expect(find(w, 'title-edit').exists()).toBe(false)
  })

  it('[승인] and the integration chip are shown as before', () => {
    const w = mountCard({ ...data, approvable: true, integration: 'pending' })
    expect(find(w, 'approve').exists()).toBe(true)
    expect(find(w, 'flags').exists()).toBe(true)
  })
})

// --- InspectorHeader ------------------------------------------------------------------------------------------

describe('InspectorHeader — the card title edit (0070 T#1)', () => {
  const F = 'p.ui.0070'
  let wrapper: VueWrapper<any> | null = null

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    i18n.global.locale.value = 'ko'
    vi.useRealTimers()
  })

  const OPEN = { editable: true, lock: null } as const
  const RUNNING = { editable: false, lock: 'running' } as const

  const card = (id: string, title = '로그인 개선'): FlowGraphNode => ({
    id, kind: 'task', title, state: 'draft', docType: 'T', x: 0, y: 0,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: 'task', label: '작업', assignee: null },
    titleState: ts(title, 1),
  })
  const targetOf = (node: FlowGraphNode, titleEdit: FlowGraphSelection['titleEdit'] = OPEN): InspectorTarget =>
    toInspectorTarget({ projectId: 'p', graph: { flowId: F, ownership: 'graph', viewMode: 'draft', latestRevisionNo: 1, nodes: [node], edges: [] }, node, titleEdit })!

  type Saver = (flowId: string, nodeKey: string, raw: string) => Promise<NodeTitleSaveOutcome>

  function mountHeader(target: InspectorTarget, saveTitle: Saver | null = vi.fn()) {
    i18n.global.locale.value = 'ko'
    const host = saveTitle ? { request: vi.fn(), saveTitle } : { request: vi.fn() }
    wrapper = mount(InspectorHeader, {
      attachTo: document.body,
      props: { target },
      global: { plugins: [i18n], provide: { [INSPECTOR_HOST as symbol]: host } },
    })
    return wrapper
  }

  const find = (w: VueWrapper<any>, test: string) => w.find(`[data-test="designer-inspector-${test}"]`)
  const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="designer-inspector-${test}"]`)
  const input = (w: VueWrapper<any>) => get(w, 'title-input').element as HTMLInputElement

  async function openByTitle(w: VueWrapper<any>) {
    await get(w, 'title').trigger('click')
    await flushPromises()
  }

  it('a click on the title turns it into an input in place: the shown title, focused, the length right of it — no ✓ / ×', async () => {
    const w = mountHeader(targetOf(card('n_a')))
    const title = get(w, 'title')
    expect(title.classes()).toContain('is-editable')
    expect(title.attributes('role')).toBe('button')
    expect(title.attributes('tabindex')).toBe('0')
    expect(title.attributes('title')).toBe('로그인 개선')
    await openByTitle(w)
    expect(find(w, 'title').exists()).toBe(false)
    expect(input(w).value).toBe('로그인 개선')
    expect(document.activeElement).toBe(input(w))
    expect(get(w, 'title-input').attributes('aria-label')).toBe('카드 제목')
    expect(get(w, 'title-input').attributes('title')).toBe('Enter 저장 · Esc 취소')
    expect(get(w, 'title-note').text()).toBe('6/30자')
    expect(get(w, 'title-note').attributes('data-tone')).toBe('hint')
    expect(w.findAll('[data-test="designer-inspector-title-editor"] button')).toHaveLength(0)
    await get(w, 'title-input').setValue('가'.repeat(31))
    expect(get(w, 'title-note').text()).toBe('31/30자')
    expect(get(w, 'title-note').attributes('data-tone')).toBe('error')
    // The header line keeps its parts.
    expect(find(w, 'more').exists()).toBe(true)
  })

  it('Enter or F2 on the focused title opens the edit as well', async () => {
    for (const key of ['Enter', 'F2']) {
      const w = mountHeader(targetOf(card('n_a')))
      await get(w, 'title').trigger('keydown', { key })
      await flushPromises()
      expect(input(w).value, key).toBe('로그인 개선')
      w.unmount()
      wrapper = null
    }
  })

  it('Enter saves the raw text for the card it was opened on; the input waits for the answer; nothing is shown before it', async () => {
    let answer!: (outcome: NodeTitleSaveOutcome) => void
    const save = vi.fn(() => new Promise<NodeTitleSaveOutcome>((resolve) => (answer = resolve)))
    const w = mountHeader(targetOf(card('n_a')), save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('  로그인   개선 요구 정리 ')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    expect(save).toHaveBeenCalledWith(F, 'n_a', '  로그인   개선 요구 정리 ')
    expect(input(w).disabled).toBe(true)
    // A second Enter or an Esc while it is answered does nothing.
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    await get(w, 'title-input').trigger('keydown', { key: 'Escape' })
    expect(save).toHaveBeenCalledTimes(1)
    expect(find(w, 'title-editor').exists()).toBe(true)
    answer({ ok: true, titleState: ts('로그인 개선 요구 정리', 2) })
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(false)
    // The title shown is the target's (the session's), never the typed text.
    expect(get(w, 'title').text()).toBe('로그인 개선')
    await w.setProps({ target: targetOf(card('n_a', '로그인 개선 요구 정리')) })
    expect(get(w, 'title').text()).toBe('로그인 개선 요구 정리')
  })

  it('Esc and a press outside cancel without saving; a press inside does not', async () => {
    const save = vi.fn()
    const w = mountHeader(targetOf(card('n_a')), save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('A')
    await get(w, 'title-input').trigger('keydown', { key: 'Escape' })
    expect(find(w, 'title-editor').exists()).toBe(false)
    expect(get(w, 'title').text()).toBe('로그인 개선')
    await openByTitle(w)
    expect(input(w).value).toBe('로그인 개선')
    input(w).dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(true)
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(false)
    expect(save).not.toHaveBeenCalled()
  })

  it('Enter / Esc while an IME composes only end the composition', async () => {
    const save = vi.fn(async () => ({ ok: true, titleState: ts('한글', 2) }) as NodeTitleSaveOutcome)
    const w = mountHeader(targetOf(card('n_a')), save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('한글')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter', isComposing: true })
    await get(w, 'title-input').trigger('keydown', { key: 'Process', keyCode: 229 })
    await get(w, 'title-input').trigger('compositionstart')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    await get(w, 'title-input').trigger('keydown', { key: 'Escape' })
    expect(save).not.toHaveBeenCalled()
    expect(find(w, 'title-editor').exists()).toBe(true)
    await get(w, 'title-input').trigger('compositionend')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(save).toHaveBeenCalledWith(F, 'n_a', '한글')
  })

  it('an empty or over-long title is refused before sending; the input stays and typing clears the reason', async () => {
    const save = vi.fn()
    const w = mountHeader(targetOf(card('n_a')), save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('  ​ ')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    expect(get(w, 'title-note').text()).toBe('제목을 입력하세요.')
    expect(get(w, 'title-note').attributes('role')).toBe('alert')
    expect(get(w, 'title-input').attributes('aria-invalid')).toBe('true')
    await get(w, 'title-input').setValue('가'.repeat(31))
    expect(get(w, 'title-note').text()).toBe('31/30자')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    expect(get(w, 'title-note').text()).toBe('제목은 30자 이하여야 합니다.')
    expect(input(w).value).toBe('가'.repeat(31))
    expect(save).not.toHaveBeenCalled()
    // 30 syllables (NFC code points, the server's count) go.
    await get(w, 'title-input').setValue('가'.repeat(30))
    expect(get(w, 'title-note').text()).toBe('30/30자')
  })

  it.each([
    ['title_empty', '제목을 입력하세요.'],
    ['title_too_long', '제목은 30자 이하여야 합니다.'],
    ['node_title_not_editable', '지금은 이 카드의 제목을 바꿀 수 없습니다.'],
    ['node_title_not_supported', '이 카드는 제목을 따로 정할 수 없습니다.'],
    ['node_not_found', '카드를 찾을 수 없습니다.'],
    ['flow_disposed', '폐기된 플로우는 변경할 수 없습니다.'],
  ])('a server refusal %s keeps the input and the edit and names it', async (code, text) => {
    const save = vi.fn(async () => ({ ok: false, code, message: 'server sentence' }) as NodeTitleSaveOutcome)
    const w = mountHeader(targetOf(card('n_a')), save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('새 제목')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(true)
    expect(input(w).value).toBe('새 제목')
    expect(input(w).disabled).toBe(false)
    expect(get(w, 'title-note').text()).toBe(text)
    expect(get(w, 'title-note').attributes('title')).toBe(text)
  })

  // rev1 (narrow Inspector): the input and its note are one item of the header line — the title area that holds the
  // title — so on a 264–560px Inspector they share the title's room instead of pushing the ✎ 메모 and ⋯ onto a line of
  // their own. jsdom has no layout (the widths were measured in Chrome on the header's own CSS), so the DOM shape is
  // checked here and the narrow rule in inspectorHeader.0051.
  it('keeps the input and its note (the length, a refusal, the lock note) inside the one title area of the line', async () => {
    const save = vi.fn(async () => ({ ok: false, code: 'node_title_not_editable', message: '' }) as NodeTitleSaveOutcome)
    const w = mountHeader(targetOf(card('n_a')), save)
    const line = get(w, 'header').element
    const area = () => get(w, 'title-area')
    expect(area().element.parentElement).toBe(line)
    expect(area().classes()).not.toContain('is-editing')
    expect(area().element.contains(get(w, 'title').element)).toBe(true)
    await openByTitle(w)
    expect(area().classes()).toContain('is-editing')
    expect(area().element.contains(get(w, 'title-editor').element)).toBe(true)
    expect(area().element.contains(get(w, 'title-note').element)).toBe(true)
    await get(w, 'title-input').setValue('새 제목')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(get(w, 'title-note').attributes('data-tone')).toBe('error')
    expect(area().element.contains(get(w, 'title-note').element)).toBe(true)
    // The note sits right of the input, and neither is an item of the line itself.
    expect(get(w, 'title-editor').element.nextElementSibling).toBe(get(w, 'title-note').element)
    const items = Array.from(line.children)
    for (const test of ['title-editor', 'title-input', 'title-note']) expect(items, test).not.toContain(get(w, test).element)
    expect(items.indexOf(area().element)).toBeLessThan(items.indexOf(get(w, 'memo').element))
    // Where the note clips (the narrowest line), the input's tooltip still names the refusal — until the text changes.
    expect(get(w, 'title-input').attributes('title')).toBe('지금은 이 카드의 제목을 바꿀 수 없습니다.')
    await get(w, 'title-input').setValue('새 제목 2')
    expect(get(w, 'title-input').attributes('title')).toBe('Enter 저장 · Esc 취소')
  })

  it('an unknown refusal shows the server’s sentence, else the general one', async () => {
    const outcomes: NodeTitleSaveOutcome[] = [{ ok: false, code: 'flow_lock_timeout', message: '잠시 후 다시 시도하세요.' }, { ok: false, code: null, message: '' }]
    const save = vi.fn(async () => outcomes.shift()!)
    const w = mountHeader(targetOf(card('n_a')), save)
    await openByTitle(w)
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(get(w, 'title-note').text()).toBe('잠시 후 다시 시도하세요.')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(get(w, 'title-note').text()).toBe('제목을 저장하지 못했습니다.')
  })

  it('node_title_locked closes the edit: the title as it was, and the lock note for a moment', async () => {
    vi.useFakeTimers()
    const save = vi.fn(async () => ({ ok: false, code: 'node_title_locked', message: '' }) as NodeTitleSaveOutcome)
    const w = mountHeader(targetOf(card('n_a')), save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('수동 정리')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(false)
    expect(get(w, 'title').text()).toBe('로그인 개선')
    expect(get(w, 'title-note').text()).toBe('실행 중인 카드의 제목은 바꿀 수 없습니다.')
    expect(get(w, 'title-note').attributes('role')).toBe('status')
    await vi.advanceTimersByTimeAsync(4000)
    expect(find(w, 'title-note').exists()).toBe(false)
  })

  it('a card that starts running while edited closes the edit with the lock note; nothing is saved; it opens no more', async () => {
    const save = vi.fn()
    const w = mountHeader(targetOf(card('n_a')), save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('입력 중')
    await w.setProps({ target: targetOf(card('n_a'), RUNNING) })
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(false)
    expect(get(w, 'title-note').text()).toBe('실행 중인 카드의 제목은 바꿀 수 없습니다.')
    expect(save).not.toHaveBeenCalled()
    const title = get(w, 'title')
    expect(title.classes()).not.toContain('is-editable')
    expect(title.attributes('role')).toBeUndefined()
    await title.trigger('click')
    await title.trigger('keydown', { key: 'F2' })
    expect(find(w, 'title-input').exists()).toBe(false)
  })

  it('a title that repeats the kind name keeps a small ✎ entry, which opens the edit with that title', async () => {
    const w = mountHeader(targetOf(card('n_a', '작업')))
    expect(find(w, 'title').exists()).toBe(false)
    const pen = get(w, 'title-edit')
    expect(pen.attributes('aria-label')).toBe('제목 편집')
    await pen.trigger('click')
    await flushPromises()
    expect(input(w).value).toBe('작업')
  })

  it('not editable — the target says no, or no host saver — shows the title only (no entry, no input)', async () => {
    for (const [target, saver] of [[targetOf(card('n_a'), null), vi.fn()], [targetOf(card('n_a')), null]] as const) {
      const w = mountHeader(target, saver)
      const title = get(w, 'title')
      expect(title.classes()).not.toContain('is-editable')
      expect(title.attributes('tabindex')).toBeUndefined()
      await title.trigger('click')
      expect(find(w, 'title-input').exists()).toBe(false)
      w.unmount()
      wrapper = null
      const repeated = mountHeader(targetOf(card('n_a', '작업'), target.canEditTitle ? OPEN : null), saver)
      expect(find(repeated, 'title-edit').exists()).toBe(false)
      repeated.unmount()
      wrapper = null
    }
  })

  it('another card selected while a save is answered: the edit closes; the late answer (or refusal) never reaches the new card', async () => {
    const answers: Array<(outcome: NodeTitleSaveOutcome) => void> = []
    const save = vi.fn(() => new Promise<NodeTitleSaveOutcome>((resolve) => answers.push(resolve)))
    const w = mountHeader(targetOf(card('n_a')), save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('A 제목')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    expect(save).toHaveBeenLastCalledWith(F, 'n_a', 'A 제목')
    await w.setProps({ target: targetOf(card('n_b', '다른 카드')) })
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(false)
    expect(get(w, 'title').text()).toBe('다른 카드')
    answers[0]({ ok: false, code: 'title_too_long', message: '' })
    await flushPromises()
    expect(find(w, 'title-input').exists()).toBe(false)
    expect(find(w, 'title-note').exists()).toBe(false)
    // An edit opened on the new card saves for the new card.
    await openByTitle(w)
    expect(input(w).value).toBe('다른 카드')
    await get(w, 'title-input').setValue('B 제목')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    expect(save).toHaveBeenLastCalledWith(F, 'n_b', 'B 제목')
  })

  it('an open edit closes when another card is selected; selecting the card again shows its stored title', async () => {
    const w = mountHeader(targetOf(card('n_a')))
    await openByTitle(w)
    await get(w, 'title-input').setValue('저장 안 한 글')
    await w.setProps({ target: targetOf(card('n_b', '다른 카드')) })
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(false)
    await w.setProps({ target: targetOf(card('n_a')) })
    await flushPromises()
    expect(get(w, 'title').text()).toBe('로그인 개선')
    expect(find(w, 'title-input').exists()).toBe(false)
  })
})

// --- On the Canvas and the Inspector ----------------------------------------------------------------------------

describe('the title edit on the real Canvas + Inspector (DesignerCenter, DesignerInspector, session)', () => {
  const P = 'p'
  const F = 'p.ui.0064'
  let wrapper: VueWrapper<any> | null = null
  let center: DesignerCenterSession | null = null

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    center?.dispose()
    center = null
  })

  const node = (id: string, x: number): FlowGraphNode => ({
    id, kind: 'task', title: `카드 ${id}`, state: 'draft', docType: 'T', x, y: 0, movable: true, structureEditable: true,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: 'task', label: `카드 ${id}`, assignee: null },
    titleState: ts(null, 0),
  })
  const GRAPH: FlowGraph = {
    flowId: F, ownership: 'graph', viewMode: 'draft', latestRevisionNo: 1,
    nodes: [node('n_a', 0), node('n_b', 300)],
    edges: [{ id: 'ge_1', source: 'n_a', target: 'n_b', edgeClass: 'execution', editable: true }],
    draft: { version: 2, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: 1 },
  }

  async function open(graph: FlowGraph = GRAPH, extra: Partial<FlowGraphSource> = {}) {
    const source: FlowGraphSource = {
      saveNodeTitle: vi.fn(async (_p, _f, _n, title: string) => ts(title.trim(), 1)),
      ...extra,
      isSample: false,
      loadGraph: vi.fn(async () => graph),
      saveDraft: vi.fn(async (_p, _f, version) => ({ version: version + 1, keyMap: {} })),
      validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
      confirmDraft: vi.fn(async () => 2),
    }
    const session = createDesignerCenterSession({ source, draftSaveDelayMs: 10 })
    center = session
    session.setProject(P)
    i18n.global.locale.value = 'ko'
    // The Canvas and the Inspector Frame on the same session, the Center column's wiring in small.
    const registry = fixtureRegistry([])
    const Screen = defineComponent({
      setup: () => () =>
        h('div', [
          h(DesignerCenter, { center: session }),
          h(DesignerInspector, {
            selection: session.activeSelection.value,
            registry,
            saveTitle: session.canEditNodeTitles ? session.saveNodeTitle : null,
          }),
        ]),
    })
    wrapper = mount(Screen, { attachTo: document.body, global: { plugins: [i18n] } })
    session.openFlow({ projectId: P, flowId: F, flowName: 'F' })
    for (let i = 0; i < 4; i += 1) await flushPromises()
    return { source, center: session }
  }

  const cardOf = (id: string) => wrapper!.findAll('[data-test="designer-canvas-node"]').find((item) => item.attributes('data-node-id') === id)!
  const header = (test: string) => wrapper!.find(`[data-test="designer-inspector-${test}"]`)
  const shown = () => (center!.state.graphs[F] as { graph: FlowGraph }).graph
  const settle = async (n = 4) => {
    for (let i = 0; i < n; i += 1) await flushPromises()
  }

  it('a title click on a card selects it and opens nothing there; the header saves through the session and both show the answer', async () => {
    const { source } = await open()
    await cardOf('n_a').get('[data-test="designer-canvas-node-title"]').trigger('click')
    await settle()
    expect(center!.state.views[F].selectedNodeIds).toEqual(['n_a'])
    expect(wrapper!.find('[data-test="designer-canvas"] input').exists()).toBe(false)
    expect(header('title').text()).toBe('카드 n_a')
    await header('title').trigger('click')
    await flushPromises()
    await header('title-input').setValue('로그인 개선')
    await header('title-input').trigger('keydown', { key: 'Enter' })
    await settle()
    expect(source.saveNodeTitle).toHaveBeenCalledWith(P, F, 'n_a', '로그인 개선')
    expect(header('title-editor').exists()).toBe(false)
    expect(header('title').text()).toBe('로그인 개선')
    expect(cardOf('n_a').get('[data-test="designer-canvas-node-title"]').text()).toBe('로그인 개선')
    expect(shown().draft?.version).toBe(2)
    expect(center!.state.edits[F].canUndo).toBe(false)
    expect(source.saveDraft).not.toHaveBeenCalled()
  })

  it('a late answer after another card was selected goes onto its own card only; the new card’s header gets nothing', async () => {
    let answer!: (state: FlowGraphTitleState) => void
    const saveNodeTitle = vi.fn(() => new Promise<FlowGraphTitleState>((resolve) => (answer = resolve)))
    await open(GRAPH, { saveNodeTitle })
    center!.selectNode(F, 'n_a')
    await settle()
    await header('title').trigger('click')
    await flushPromises()
    await header('title-input').setValue('A 제목')
    await header('title-input').trigger('keydown', { key: 'Enter' })
    expect(saveNodeTitle).toHaveBeenCalledWith(P, F, 'n_a', 'A 제목')
    center!.selectNode(F, 'n_b')
    await settle()
    expect(header('title-editor').exists()).toBe(false)
    expect(header('title').text()).toBe('카드 n_b')
    answer(ts('A 제목', 1))
    await settle()
    expect(cardOf('n_a').get('[data-test="designer-canvas-node-title"]').text()).toBe('A 제목')
    expect(cardOf('n_b').get('[data-test="designer-canvas-node-title"]').text()).toBe('카드 n_b')
    expect(header('title').text()).toBe('카드 n_b')
    expect(header('title-input').exists()).toBe(false)
    expect(header('title-note').exists()).toBe(false)
  })

  /** A run source whose current run is `run.current` (read again with the Graph). */
  function runSource(run: { current: FlowRunInfo }): Partial<FlowGraphSource> {
    return {
      loadRun: vi.fn(async () => run.current),
      startRun: vi.fn(async () => run.current),
      stopRun: vi.fn(async () => run.current),
      resumeRun: vi.fn(async () => run.current),
    }
  }
  const runOf = (nodeRuns: unknown[]): FlowRunInfo => ({ runId: 'run_1', status: 'idle', openNodeKeys: [], nodeRuns: nodeRuns as FlowRunNodeInfo[] })

  it('a draft card the run read shows waiting for approval opens; its AI review starting while edited closes the edit with the lock note', async () => {
    // A draft view: its nodes carry no node run — the run read's node run (the latest) is the card's, as a whole.
    const run = { current: runOf([{ nodeKey: 'n_a', status: 'review', resultDocId: 'T-1', awaitingApproval: true, nodeRunning: false }]) }
    const { source } = await open(GRAPH, runSource(run))
    center!.selectNode(F, 'n_a')
    await settle()
    expect(shown().nodes.every((n) => !n.nodeRun)).toBe(true)
    expect(header('title').classes()).toContain('is-editable')
    await header('title').trigger('click')
    await flushPromises()
    await header('title-input').setValue('검수 전에 고친 제목')
    run.current = runOf([{ nodeKey: 'n_a', status: 'review', resultDocId: 'T-1', awaitingApproval: false, nodeRunning: true }])
    center!.refreshFlow(F)
    await settle(6)
    expect(header('title-editor').exists()).toBe(false)
    expect(header('title-note').text()).toBe('실행 중인 카드의 제목은 바꿀 수 없습니다.')
    expect(header('title').classes()).not.toContain('is-editable')
    expect(header('title').text()).toBe('카드 n_a')
    expect(source.saveNodeTitle).not.toHaveBeenCalled()
  })

  it('a card whose run is open shows no edit entry in the header; the other card’s title opens', async () => {
    const running: FlowGraph = { ...GRAPH, nodes: [{ ...node('n_a', 0), nodeRun: { status: 'open', openedAt: null, finishedAt: null, resultDocId: null } }, node('n_b', 300)] }
    await open(running)
    center!.selectNode(F, 'n_a')
    await settle()
    expect(header('title').classes()).not.toContain('is-editable')
    await header('title').trigger('click')
    expect(header('title-input').exists()).toBe(false)
    center!.selectNode(F, 'n_b')
    await settle()
    expect(header('title').classes()).toContain('is-editable')
  })

  it('a source that saves no titles opens no header edit', async () => {
    await open(GRAPH, { saveNodeTitle: undefined })
    center!.selectNode(F, 'n_a')
    await settle()
    expect(center!.activeSelection.value?.titleEdit ?? null).toBeNull()
    expect(header('title').classes()).not.toContain('is-editable')
  })

  it('the Canvas key handler ignores keys from any text field', async () => {
    await open()
    center!.selectNode(F, 'n_a')
    await flushPromises()
    const removeNode = vi.spyOn(center!, 'removeNode')
    const undoEdit = vi.spyOn(center!, 'undoEdit')
    const canvas = wrapper!.get('[data-test="designer-canvas"]').element
    const field = document.createElement('input')
    canvas.appendChild(field)
    for (const init of [{ key: 'Delete' }, { key: 'Backspace' }, { key: 'z', ctrlKey: true }]) {
      field.dispatchEvent(new KeyboardEvent('keydown', { ...init, bubbles: true }))
    }
    await flushPromises()
    expect(removeNode).not.toHaveBeenCalled()
    expect(undoEdit).not.toHaveBeenCalled()
    field.remove()
    // The same key on the Canvas itself still removes the selected draft node.
    canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }))
    await flushPromises()
    expect(removeNode).toHaveBeenCalledWith(F, 'n_a')
  })
})

// --- The Center column's wiring ---------------------------------------------------------------------------------

describe('the Center column carries the header’s title save to the session', () => {
  it('saveNodeTitle with the Flow and card given (not the active Flow); none when the session saves no titles', async () => {
    const seen: Array<unknown> = []
    const InspectorStub = defineComponent({
      name: 'DesignerInspector',
      props: { selection: { type: Object, default: null }, unselected: Boolean, saveTitle: { type: Function, default: null } },
      setup: (props) => () => {
        seen.push(props.saveTitle)
        return h('section', { 'data-test': 'inspector-stub' })
      },
    })
    const stubs = {
      DesignerWorkspace: defineComponent({ name: 'DesignerWorkspace', render: () => h('section') }),
      DesignerMiniMap: defineComponent({ name: 'DesignerMiniMap', render: () => h('aside') }),
      DesignerInspector: InspectorStub,
    }
    const answer: NodeTitleSaveOutcome = { ok: true, titleState: ts('X', 1) }
    const fake = (canEditNodeTitles: boolean) => ({
      canEditNodeTitles,
      saveNodeTitle: vi.fn(async () => answer),
      activeSelection: { value: null },
      activeSelectedNode: { value: null },
      activeGraphLoad: { value: null },
      activeView: { value: null },
      canvasSize: { value: null },
      state: { activeFlowId: 'other.flow' },
    })
    const saving = fake(true)
    const w = mount(DesignerCenterColumn, { props: { center: saving as unknown as DesignerCenterSession, bottomHeight: 300 }, global: { plugins: [i18n], stubs } })
    const saveTitle = seen.at(-1) as (flowId: string, nodeId: string, title: string) => Promise<NodeTitleSaveOutcome>
    expect(typeof saveTitle).toBe('function')
    expect(await saveTitle('p.ui.0070', 'n_a', ' 새 제목 ')).toBe(answer)
    expect(saving.saveNodeTitle).toHaveBeenCalledWith('p.ui.0070', 'n_a', ' 새 제목 ')
    w.unmount()

    seen.length = 0
    const none = mount(DesignerCenterColumn, { props: { center: fake(false) as unknown as DesignerCenterSession, bottomHeight: 300 }, global: { plugins: [i18n], stubs } })
    expect(seen.at(-1)).toBeNull()
    none.unmount()
  })
})
