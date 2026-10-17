/**
 * flowgate.v02.0064 T#4 (0062 D0005 §3.2 / §6 세트 2, 0064 P0006 §6.5) — the Canvas card's inline title edit:
 *
 * - FlowNodeCard: a click on an editable title (or ✎ — hover only, also for a title that repeats the kind name) opens
 *   an input with ✓ / × and the 30 character guidance; Enter / ✓ save, Esc / × cancel, Enter while an IME composes does
 *   not save, a click outside cancels; the screen refuses an empty / over-long title before sending; a server refusal
 *   keeps the input and names it, `node_title_locked` closes the edit with the lock note; a card that starts running
 *   while edited closes with the note; a card not editable (no title state, running, no saver) opens nothing and its
 *   title click is the card's selection as before; nothing opens while a connection is dragged;
 * - isolation: the input and its buttons keep pointer and key events (drag, pan, selection, remove, undo) to themselves —
 *   also a key on ✓ / × focused by Tab, where Enter / Space stay the button's own press and Esc cancels;
 * - on the real Canvas (DesignerCenter + session): Delete / Backspace / Ctrl+Z typed into the title or pressed on a focused
 *   ✓ / × never remove a node or undo (Nodes, Edges, selection and the structure history as they were), the Canvas key
 *   handler ignores keys from any text field and from anything in a title edit, a title click in [선택] mode leaves the
 *   multi-selection as it is, and a saved title shows on the card at once with the draft and its history untouched;
 * - the running lock through the real mapping and run read: a draft card the run read shows waiting for its approval
 *   opens, and its AI review starting while it is edited (the run read's `node_running`) closes the edit with the lock
 *   note before anything is saved.
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
import type { FlowGraph, FlowGraphNode, FlowGraphTitleState } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunInfo, FlowRunNodeInfo } from '@/features/designer/center/graph/flowGraphSource'
import type { NodeTitleSaveOutcome } from '@/features/designer/center/graph/nodeTitle'

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

describe('FlowNodeCard — the inline title edit', () => {
  let wrapper: VueWrapper<any> | null = null
  const outer = { click: vi.fn(), pointerdown: vi.fn(), mousedown: vi.fn(), keydown: vi.fn(), keyup: vi.fn(), dblclick: vi.fn() }

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    for (const spy of Object.values(outer)) spy.mockReset()
    i18n.global.locale.value = 'ko'
    vi.useRealTimers()
  })

  const editable: FlowGateNodeData = { kind: 'task', docType: 'T', title: '로그인 개선', state: 'draft', selected: false, titleEditable: true }

  /** The card inside a host that records what reaches it (the Vue Flow node wrapper's place). */
  function mountCard(data: FlowGateNodeData, saveTitle: ((id: string, title: string) => Promise<NodeTitleSaveOutcome>) | null = vi.fn(), connecting = false) {
    const Host = defineComponent({
      props: { data: { type: Object, required: true }, connecting: Boolean },
      setup: (props) => () =>
        h('div', { 'data-test': 'host', onClick: outer.click, onPointerdown: outer.pointerdown, onMousedown: outer.mousedown, onKeydown: outer.keydown, onKeyup: outer.keyup, onDblclick: outer.dblclick }, [
          h(FlowNodeCard, { id: 'n_1', data: props.data as FlowGateNodeData, saveTitle, connecting: props.connecting }),
        ]),
    })
    i18n.global.locale.value = 'ko'
    wrapper = mount(Host, { props: { data, connecting }, attachTo: document.body, global: { plugins: [i18n], stubs: { Handle: true } } })
    return wrapper
  }

  const find = (w: VueWrapper<any>, test: string) => w.find(`[data-test="designer-canvas-node-${test}"]`)
  const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="designer-canvas-node-${test}"]`)
  const input = (w: VueWrapper<any>) => get(w, 'title-input').element as HTMLInputElement

  async function openByTitle(w: VueWrapper<any>) {
    await get(w, 'title').trigger('click')
    await flushPromises()
  }

  it('a click on the title opens the input with the shown title, ✓ / × and the 30 character guidance — not the selection', async () => {
    const w = mountCard(editable)
    expect(get(w, 'title').classes()).toContain('is-editable')
    expect(get(w, 'title-edit').attributes('aria-label')).toBe('제목 편집')
    await openByTitle(w)
    expect(outer.click).not.toHaveBeenCalled()
    expect(find(w, 'title').exists()).toBe(false)
    expect(input(w).value).toBe('로그인 개선')
    expect(document.activeElement).toBe(input(w))
    expect(get(w, 'title-save').text()).toBe('✓')
    expect(get(w, 'title-cancel').text()).toBe('×')
    expect(get(w, 'title-note').text()).toBe('6/30자')
    expect(get(w, 'title-note').attributes('data-tone')).toBe('hint')
    await get(w, 'title-input').setValue('가'.repeat(31))
    expect(get(w, 'title-note').text()).toBe('31/30자')
    expect(get(w, 'title-note').attributes('data-tone')).toBe('error')
  })

  it('the input and its buttons keep pointer and key events to themselves (no drag, pan, selection, remove, undo)', async () => {
    const w = mountCard(editable)
    await openByTitle(w)
    for (const part of ['title-editor', 'title-input', 'title-save', 'title-cancel']) {
      expect(get(w, part).classes(), part).toEqual(expect.arrayContaining(['nodrag', 'nopan']))
    }
    for (const part of ['title-input', 'title-save', 'title-cancel']) {
      await get(w, part).trigger('pointerdown')
      await get(w, part).trigger('mousedown')
      await get(w, part).trigger('dblclick')
    }
    await get(w, 'title-input').trigger('click')
    for (const key of [{ key: 'Delete' }, { key: 'Backspace' }, { key: 'z', ctrlKey: true }, { key: 'z', metaKey: true }, { key: 'a' }]) {
      await get(w, 'title-input').trigger('keydown', key)
    }
    expect(outer.pointerdown).not.toHaveBeenCalled()
    expect(outer.mousedown).not.toHaveBeenCalled()
    expect(outer.dblclick).not.toHaveBeenCalled()
    expect(outer.click).not.toHaveBeenCalled()
    expect(outer.keydown).not.toHaveBeenCalled()
    expect(find(w, 'title-editor').exists()).toBe(true)
  })

  it('keys on a focused ✓ / × stay in the edit; Enter / Space there are the button’s own press, Esc cancels', async () => {
    const save = vi.fn(async () => ({ ok: true, titleState: ts('A', 1) }) as NodeTitleSaveOutcome)
    const w = mountCard(editable, save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('A')
    for (const part of ['title-save', 'title-cancel']) {
      const button = get(w, part).element as HTMLButtonElement
      button.focus()
      expect(document.activeElement).toBe(button)
      for (const init of [{ key: 'Delete' }, { key: 'Backspace' }, { key: 'z', ctrlKey: true }, { key: 'z', metaKey: true }, { key: 'Enter' }, { key: ' ' }]) {
        const down = new KeyboardEvent('keydown', { ...init, bubbles: true, cancelable: true })
        const up = new KeyboardEvent('keyup', { ...init, bubbles: true, cancelable: true })
        button.dispatchEvent(down)
        button.dispatchEvent(up)
        // Not cancelled: the browser still presses the focused button on Enter / Space (✓ saves, × cancels by its click).
        expect(down.defaultPrevented, `${part} ${init.key}`).toBe(false)
        expect(up.defaultPrevented, `${part} ${init.key}`).toBe(false)
      }
    }
    expect(outer.keydown).not.toHaveBeenCalled()
    expect(outer.keyup).not.toHaveBeenCalled()
    // Enter on a button is not the input's Enter: nothing saved by the key itself, the edit and the text stay.
    expect(save).not.toHaveBeenCalled()
    expect(find(w, 'title-editor').exists()).toBe(true)
    expect(input(w).value).toBe('A')
    const cancelButton = get(w, 'title-cancel').element as HTMLButtonElement
    cancelButton.focus()
    cancelButton.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(false)
    expect(save).not.toHaveBeenCalled()
    expect(outer.keydown).not.toHaveBeenCalled()
  })

  it('Enter saves the text as typed; the input and buttons wait for the answer; a saved answer closes the edit', async () => {
    let answer!: (outcome: NodeTitleSaveOutcome) => void
    const save = vi.fn(() => new Promise<NodeTitleSaveOutcome>((resolve) => (answer = resolve)))
    const w = mountCard(editable, save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('  로그인   개선 요구 정리 ')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    expect(save).toHaveBeenCalledWith('n_1', '  로그인   개선 요구 정리 ')
    expect(input(w).disabled).toBe(true)
    expect(get(w, 'title-save').attributes('disabled')).toBeDefined()
    expect(get(w, 'title-cancel').attributes('disabled')).toBeDefined()
    // Escape or a second Enter while it is answered do nothing.
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    expect(save).toHaveBeenCalledTimes(1)
    answer({ ok: true, titleState: ts('로그인 개선 요구 정리', 1) })
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(false)
    // The title shown is the card data's (the session's), never the typed text.
    expect(get(w, 'title').text()).toBe('로그인 개선')
    await w.setProps({ data: { ...editable, title: '로그인 개선 요구 정리' } })
    expect(get(w, 'title').text()).toBe('로그인 개선 요구 정리')
  })

  it('✓ saves; Esc and × cancel without saving', async () => {
    const save = vi.fn(async () => ({ ok: true, titleState: ts('A', 1) }) as NodeTitleSaveOutcome)
    const w = mountCard(editable, save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('A')
    await get(w, 'title-input').trigger('keydown', { key: 'Escape' })
    expect(find(w, 'title-editor').exists()).toBe(false)
    await openByTitle(w)
    expect(input(w).value).toBe('로그인 개선')
    await get(w, 'title-cancel').trigger('click')
    expect(find(w, 'title-editor').exists()).toBe(false)
    expect(save).not.toHaveBeenCalled()
    await openByTitle(w)
    await get(w, 'title-input').setValue('A')
    await get(w, 'title-save').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith('n_1', 'A')
    expect(find(w, 'title-editor').exists()).toBe(false)
  })

  it('Enter / Esc while an IME composes only end the composition', async () => {
    const save = vi.fn(async () => ({ ok: true, titleState: ts('한글', 1) }) as NodeTitleSaveOutcome)
    const w = mountCard(editable, save)
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
    expect(save).toHaveBeenCalledWith('n_1', '한글')
  })

  it('an empty or over-long title is refused before sending; the input stays and typing clears the reason', async () => {
    const save = vi.fn()
    const w = mountCard(editable, save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('  ​ ')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    expect(get(w, 'title-note').text()).toBe('제목을 입력하세요.')
    expect(get(w, 'title-note').attributes('role')).toBe('alert')
    expect(get(w, 'title-input').attributes('aria-invalid')).toBe('true')
    await get(w, 'title-input').setValue('가'.repeat(31))
    expect(get(w, 'title-note').text()).toBe('31/30자')
    await get(w, 'title-save').trigger('click')
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
  ])('a server refusal %s keeps the input and names it', async (code, text) => {
    const save = vi.fn(async () => ({ ok: false, code, message: 'server sentence' }) as NodeTitleSaveOutcome)
    const w = mountCard(editable, save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('새 제목')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(true)
    expect(input(w).value).toBe('새 제목')
    expect(input(w).disabled).toBe(false)
    expect(get(w, 'title-note').text()).toBe(text)
  })

  it('an unknown refusal shows the server’s sentence, else the general one; en / ja have the sentences too', async () => {
    const outcomes: NodeTitleSaveOutcome[] = [{ ok: false, code: 'flow_lock_timeout', message: '잠시 후 다시 시도하세요.' }, { ok: false, code: null, message: '' }]
    const save = vi.fn(async () => outcomes.shift()!)
    const w = mountCard(editable, save)
    await openByTitle(w)
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(get(w, 'title-note').text()).toBe('잠시 후 다시 시도하세요.')
    await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(get(w, 'title-note').text()).toBe('제목을 저장하지 못했습니다.')
    for (const [locale, text] of [['en', 'Enter a title.'], ['ja', 'タイトルを入力してください。']]) {
      i18n.global.locale.value = locale
      await get(w, 'title-input').setValue(' ')
      await get(w, 'title-input').trigger('keydown', { key: 'Enter' })
      expect(get(w, 'title-note').text()).toBe(text)
    }
  })

  it('node_title_locked closes the edit: the title as it was, and the lock note', async () => {
    vi.useFakeTimers()
    const save = vi.fn(async () => ({ ok: false, code: 'node_title_locked', message: '' }) as NodeTitleSaveOutcome)
    const w = mountCard(editable, save)
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

  it('a card that starts running while edited closes the edit with the lock note; nothing is saved', async () => {
    const save = vi.fn()
    const w = mountCard(editable, save)
    await openByTitle(w)
    await get(w, 'title-input').setValue('입력 중')
    await w.setProps({ data: { ...editable, titleEditable: undefined, titleLock: 'running' } })
    expect(find(w, 'title-editor').exists()).toBe(false)
    expect(get(w, 'title-note').text()).toBe('실행 중인 카드의 제목은 바꿀 수 없습니다.')
    expect(save).not.toHaveBeenCalled()
    // A locked card opens nothing; its title click is the card's selection.
    expect(find(w, 'title-edit').exists()).toBe(false)
    await get(w, 'title').trigger('click')
    expect(find(w, 'title-editor').exists()).toBe(false)
    expect(outer.click).toHaveBeenCalledTimes(1)
  })

  it('a newer title arriving while edited keeps the input; × then shows the newer title', async () => {
    const w = mountCard(editable)
    await openByTitle(w)
    await get(w, 'title-input').setValue('내가 쓰는 중')
    await w.setProps({ data: { ...editable, title: '다른 탭 제목' } })
    expect(input(w).value).toBe('내가 쓰는 중')
    await get(w, 'title-cancel').trigger('click')
    expect(get(w, 'title').text()).toBe('다른 탭 제목')
  })

  it('a press outside cancels; a press inside does not', async () => {
    const save = vi.fn()
    const w = mountCard(editable, save)
    await openByTitle(w)
    get(w, 'title-input').element.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(true)
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(find(w, 'title-editor').exists()).toBe(false)
    expect(save).not.toHaveBeenCalled()
  })

  it('a card whose title repeats its kind name shows only ✎ (hover), which opens the edit with that title', async () => {
    const w = mountCard({ ...editable, title: '작업' })
    expect(find(w, 'title').exists()).toBe(false)
    await get(w, 'title-edit').trigger('click')
    await flushPromises()
    expect(input(w).value).toBe('작업')
    expect(outer.click).not.toHaveBeenCalled()
  })

  it('not editable — no title state, no saver, a connection being dragged, or a drag that ended on the title — opens nothing', async () => {
    const plain = mountCard({ ...editable, titleEditable: undefined })
    expect(find(plain, 'title-edit').exists()).toBe(false)
    expect(find(plain, 'title').classes()).not.toContain('is-editable')
    await openByTitle(plain)
    expect(find(plain, 'title-editor').exists()).toBe(false)
    expect(outer.click).toHaveBeenCalledTimes(1)
    plain.unmount()

    const noSaver = mountCard(editable, null)
    expect(find(noSaver, 'title-edit').exists()).toBe(false)
    noSaver.unmount()

    const connecting = mountCard(editable, vi.fn(), true)
    await openByTitle(connecting)
    expect(find(connecting, 'title-editor').exists()).toBe(false)
    connecting.unmount()

    const dragged = mountCard(editable)
    await get(dragged, 'title').trigger('pointerdown', { clientX: 0, clientY: 0 })
    await get(dragged, 'title').trigger('click', { clientX: 24, clientY: 2 })
    expect(find(dragged, 'title-editor').exists()).toBe(false)
    await get(dragged, 'title').trigger('pointerdown', { clientX: 0, clientY: 0 })
    await get(dragged, 'title').trigger('click', { clientX: 1, clientY: 1 })
    expect(find(dragged, 'title-editor').exists()).toBe(true)
  })

  it('[승인] and the integration chip step aside while the title is edited', async () => {
    const w = mountCard({ ...editable, approvable: true, integration: 'pending' })
    expect(find(w, 'approve').exists()).toBe(true)
    await openByTitle(w)
    expect(find(w, 'approve').exists()).toBe(false)
    expect(find(w, 'flags').exists()).toBe(false)
    await get(w, 'title-cancel').trigger('click')
    expect(find(w, 'approve').exists()).toBe(true)
  })
})

// --- On the Canvas -------------------------------------------------------------------------------------------

describe('the title edit on the Canvas (DesignerCenter + session)', () => {
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
      ...extra,
      isSample: false,
      loadGraph: vi.fn(async () => graph),
      saveDraft: vi.fn(async (_p, _f, version) => ({ version: version + 1, keyMap: {} })),
      validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
      confirmDraft: vi.fn(async () => 2),
      saveNodeTitle: vi.fn(async (_p, _f, _n, title: string) => ts(title.trim(), 1)),
    }
    center = createDesignerCenterSession({ source, draftSaveDelayMs: 10 })
    center.setProject(P)
    i18n.global.locale.value = 'ko'
    wrapper = mount(DesignerCenter, { props: { center }, attachTo: document.body, global: { plugins: [i18n] } })
    center.openFlow({ projectId: P, flowId: F, flowName: 'F' })
    for (let i = 0; i < 4; i += 1) await flushPromises()
    return { source, center }
  }

  const cardOf = (id: string) => wrapper!.findAll('[data-test="designer-canvas-node"]').find((item) => item.attributes('data-node-id') === id)!
  const shown = () => (center!.state.graphs[F] as { graph: FlowGraph }).graph

  it('Delete / Backspace / Ctrl+Z typed into the title remove nothing and undo nothing; the saved title shows at once', async () => {
    const { source } = await open()
    center!.selectNode(F, 'n_a')
    await flushPromises()
    const removeNode = vi.spyOn(center!, 'removeNode')
    const undoEdit = vi.spyOn(center!, 'undoEdit')
    await cardOf('n_a').get('[data-test="designer-canvas-node-title"]').trigger('click')
    await flushPromises()
    // The click opened the edit and left the selection alone.
    expect(center!.state.views[F].selectedNodeIds).toEqual(['n_a'])
    const field = cardOf('n_a').get('[data-test="designer-canvas-node-title-input"]')
    for (const key of [{ key: 'Delete' }, { key: 'Backspace' }, { key: 'z', ctrlKey: true }, { key: 'z', metaKey: true }]) {
      await field.trigger('keydown', key)
    }
    expect(removeNode).not.toHaveBeenCalled()
    expect(undoEdit).not.toHaveBeenCalled()
    expect(shown().nodes.map((n) => n.id)).toEqual(['n_a', 'n_b'])
    await field.setValue('로그인 개선')
    await field.trigger('keydown', { key: 'Enter' })
    for (let i = 0; i < 4; i += 1) await flushPromises()
    expect(source.saveNodeTitle).toHaveBeenCalledWith(P, F, 'n_a', '로그인 개선')
    expect(cardOf('n_a').get('[data-test="designer-canvas-node-title"]').text()).toBe('로그인 개선')
    expect(shown().draft?.version).toBe(2)
    expect(center!.state.edits[F].canUndo).toBe(false)
    expect(source.saveDraft).not.toHaveBeenCalled()
  })

  it('the Canvas key handler ignores keys from any text field (defence beside the card’s own)', async () => {
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

  it('Delete / Backspace / Ctrl+Z on a focused ✓ or × remove nothing and undo nothing: Nodes, Edges, selection and history stay', async () => {
    const { source } = await open()
    // A structure history first (a card move shares the undo), so a leaked Ctrl+Z would have something to undo.
    center!.moveNode(F, 'n_b', 420, 0)
    center!.selectNode(F, 'n_a')
    for (let i = 0; i < 4; i += 1) await flushPromises()
    expect(center!.state.edits[F].canUndo).toBe(true)
    const removeNode = vi.spyOn(center!, 'removeNode')
    const undoEdit = vi.spyOn(center!, 'undoEdit')
    await cardOf('n_a').get('[data-test="designer-canvas-node-title"]').trigger('click')
    await flushPromises()
    await cardOf('n_a').get('[data-test="designer-canvas-node-title-input"]').setValue('입력 중')
    const before = {
      nodes: shown().nodes.map((n) => [n.id, n.x, n.y]),
      edges: shown().edges.map((e) => e.id),
      selected: [...center!.state.views[F].selectedNodeIds],
      canUndo: center!.state.edits[F].canUndo,
      canRedo: center!.state.edits[F].canRedo,
    }
    for (const part of ['save', 'cancel']) {
      const button = cardOf('n_a').get(`[data-test="designer-canvas-node-title-${part}"]`).element as HTMLButtonElement
      button.focus()
      expect(document.activeElement).toBe(button)
      for (const init of [{ key: 'Delete' }, { key: 'Backspace' }, { key: 'z', ctrlKey: true }, { key: 'z', metaKey: true }]) {
        button.dispatchEvent(new KeyboardEvent('keydown', { ...init, bubbles: true, cancelable: true }))
        button.dispatchEvent(new KeyboardEvent('keyup', { ...init, bubbles: true, cancelable: true }))
      }
      for (let i = 0; i < 2; i += 1) await flushPromises()
    }
    expect(removeNode).not.toHaveBeenCalled()
    expect(undoEdit).not.toHaveBeenCalled()
    expect({
      nodes: shown().nodes.map((n) => [n.id, n.x, n.y]),
      edges: shown().edges.map((e) => e.id),
      selected: [...center!.state.views[F].selectedNodeIds],
      canUndo: center!.state.edits[F].canUndo,
      canRedo: center!.state.edits[F].canRedo,
    }).toEqual(before)
    expect(before.nodes).toEqual([['n_a', 0, 0], ['n_b', 420, 0]])
    expect(before.selected).toEqual(['n_a'])
    // The edit is still open with its text; nothing was saved by those keys.
    expect((cardOf('n_a').get('[data-test="designer-canvas-node-title-input"]').element as HTMLInputElement).value).toBe('입력 중')
    expect(source.saveNodeTitle).not.toHaveBeenCalled()
  })

  it('the Canvas key handler also ignores keys from anything inside a title edit (its ✓ / × included)', async () => {
    await open()
    center!.moveNode(F, 'n_b', 420, 0)
    center!.selectNode(F, 'n_a')
    for (let i = 0; i < 4; i += 1) await flushPromises()
    const removeNode = vi.spyOn(center!, 'removeNode')
    const undoEdit = vi.spyOn(center!, 'undoEdit')
    const canvas = wrapper!.get('[data-test="designer-canvas"]').element
    // An edit whose own guard is bypassed: only the Canvas's defence stands between the key and the Graph.
    const editor = document.createElement('div')
    editor.className = 'flow-node-title-editor'
    const button = document.createElement('button')
    editor.appendChild(button)
    canvas.appendChild(editor)
    for (const init of [{ key: 'Delete' }, { key: 'Backspace' }, { key: 'z', ctrlKey: true }, { key: 'z', metaKey: true }]) {
      button.dispatchEvent(new KeyboardEvent('keydown', { ...init, bubbles: true }))
    }
    await flushPromises()
    expect(removeNode).not.toHaveBeenCalled()
    expect(undoEdit).not.toHaveBeenCalled()
    editor.remove()
    // A button outside a title edit is not shielded: the Canvas keeps its keys.
    const plain = document.createElement('button')
    canvas.appendChild(plain)
    plain.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true }))
    await flushPromises()
    expect(undoEdit).toHaveBeenCalledWith(F)
    plain.remove()
  })

  it('in [선택] mode a title click keeps the multi-selection and drags nothing', async () => {
    await open()
    center!.setSelectMode(true)
    center!.selectNodes(F, ['n_a', 'n_b'])
    await flushPromises()
    await cardOf('n_b').get('[data-test="designer-canvas-node-title"]').trigger('click')
    await flushPromises()
    expect(center!.state.views[F].selectedNodeIds).toEqual(['n_a', 'n_b'])
    expect(cardOf('n_b').find('[data-test="designer-canvas-node-title-input"]').exists()).toBe(true)
    expect(shown().nodes.map((n) => [n.id, n.x])).toEqual([['n_a', 0], ['n_b', 300]])
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

  it('a draft card the run read shows waiting for approval opens; its AI review starting while edited closes the edit with the lock note, nothing saved', async () => {
    // A draft view: its nodes carry no node run — the run read's node run (the latest) is the card's, as a whole.
    const run = { current: runOf([{ nodeKey: 'n_a', status: 'review', resultDocId: 'T-1', awaitingApproval: true, nodeRunning: false }]) }
    const { source } = await open(GRAPH, runSource(run))
    expect(shown().nodes.every((n) => !n.nodeRun)).toBe(true)
    expect(cardOf('n_a').find('[data-test="designer-canvas-node-title-edit"]').exists()).toBe(true)
    await cardOf('n_a').get('[data-test="designer-canvas-node-title"]').trigger('click')
    await flushPromises()
    const field = cardOf('n_a').get('[data-test="designer-canvas-node-title-input"]')
    await field.setValue('검수 전에 고친 제목')
    // The card's AI review starts: still review, no longer awaiting approval, and running by the server's lock.
    run.current = runOf([{ nodeKey: 'n_a', status: 'review', resultDocId: 'T-1', awaitingApproval: false, nodeRunning: true }])
    center!.refreshFlow(F)
    for (let i = 0; i < 6; i += 1) await flushPromises()
    expect(cardOf('n_a').find('[data-test="designer-canvas-node-title-editor"]').exists()).toBe(false)
    expect(cardOf('n_a').get('[data-test="designer-canvas-node-title-note"]').text()).toBe('실행 중인 카드의 제목은 바꿀 수 없습니다.')
    expect(cardOf('n_a').find('[data-test="designer-canvas-node-title-edit"]').exists()).toBe(false)
    expect(cardOf('n_a').get('[data-test="designer-canvas-node-title"]').text()).toBe('카드 n_a')
    expect(source.saveNodeTitle).not.toHaveBeenCalled()
  })

  it('a review card that waits only for its result integration (not running by the server’s lock) opens and saves', async () => {
    const run = { current: runOf([{ nodeKey: 'n_a', status: 'review', resultDocId: 'T-1', awaitingApproval: false, integrationStatus: 'pending', nodeRunning: false }]) }
    const { source } = await open(GRAPH, runSource(run))
    expect(cardOf('n_a').find('[data-test="designer-canvas-node-title-edit"]').exists()).toBe(true)
    await cardOf('n_a').get('[data-test="designer-canvas-node-title"]').trigger('click')
    await flushPromises()
    const field = cardOf('n_a').get('[data-test="designer-canvas-node-title-input"]')
    await field.setValue('병합 대기 카드')
    await field.trigger('keydown', { key: 'Enter' })
    for (let i = 0; i < 4; i += 1) await flushPromises()
    expect(source.saveNodeTitle).toHaveBeenCalledWith(P, F, 'n_a', '병합 대기 카드')
    expect(cardOf('n_a').get('[data-test="designer-canvas-node-title"]').text()).toBe('병합 대기 카드')
  })

  it('a card whose run is open shows no edit, its title click selects it; the other card opens', async () => {
    const running: FlowGraph = { ...GRAPH, nodes: [{ ...node('n_a', 0), nodeRun: { status: 'open', openedAt: null, finishedAt: null, resultDocId: null } }, node('n_b', 300)] }
    await open(running)
    expect(cardOf('n_a').find('[data-test="designer-canvas-node-title-edit"]').exists()).toBe(false)
    expect(cardOf('n_b').find('[data-test="designer-canvas-node-title-edit"]').exists()).toBe(true)
    await cardOf('n_a').get('[data-test="designer-canvas-node-title"]').trigger('click')
    await flushPromises()
    expect(cardOf('n_a').find('[data-test="designer-canvas-node-title-input"]').exists()).toBe(false)
    expect(center!.state.views[F].selectedNodeIds).toEqual(['n_a'])
  })
})
