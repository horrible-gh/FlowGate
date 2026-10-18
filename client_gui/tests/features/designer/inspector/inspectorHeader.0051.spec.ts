/**
 * flowgate.v02.0051 — the v7 header line on every card (InspectorHeader): the 읽기 전용 tag from the
 * target, the ✎ 메모 button shown but disabled (no memo API yet), the ● state pill, and the ⋯ menu
 * with ID 복사 — enabled with a document and the clipboard, copying the bound document's id.
 * flowgate.v02.0052 묶음 A — the single status pill: one user-facing label (the Canvas state first),
 * a `· sub` only for a server state that says something else, never an internal value as text, the
 * server values in the tooltip and the data attributes.
 * flowgate.v02.0070 T#4 (B4) — one kind display (flowNodeKindView) for the Canvas card head and the header: a requirement
 * Node with a B document is 버그 (bug glyph, red accent) in both, an R one 요구사항; a title that only repeats it hides.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { h } from 'vue'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { flowNodeKindDisplay, flowNodeKindView } from '@/features/designer/center/canvas/flowNodeKinds'
import type { FlowGateNodeData } from '@/features/designer/center/canvas/vueFlowMapping'
import type { FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import InspectorHeader from '@/features/designer/inspector/InspectorHeader.vue'
import { prepareInspectorI18n, SELECTIONS, selectionOf, targetOf, UNASSIGNED_GRAPH, UNASSIGNED_REQ_NODE } from './support/inspectorSupport'

const i18n = prepareInspectorI18n()

let wrapper: VueWrapper<any> | null = null
let writeText: ReturnType<typeof vi.fn>

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  writeText = vi.fn(async () => undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  Reflect.deleteProperty(navigator, 'clipboard')
})

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)

function mountHeader(selection: (typeof SELECTIONS)[keyof typeof SELECTIONS]) {
  wrapper = mount(InspectorHeader, { attachTo: document.body, props: { target: targetOf(selection) }, global: { plugins: [i18n] } })
  return wrapper
}

async function openMenu(w: VueWrapper<any>) {
  await get(w, 'designer-inspector-more').trigger('click')
  await flushPromises()
  const item = document.body.querySelector<HTMLButtonElement>('[data-test="designer-inspector-copy-id"]')
  if (!item) throw new Error('menu not opened')
  return item
}

describe('InspectorHeader (flowgate.v02.0051)', () => {
  // flowgate.v02.0053 T#9: the header alone (no memo part in its `memo` slot) keeps the dim, disabled ✎ 메모 —
  // what a Node without a document shows; a card's working ✎ 메모 is features/memo (memo.0053).
  it('shows ✎ 메모 disabled with its reason when no memo part fills it, and the ⋯ button', () => {
    const w = mountHeader(SELECTIONS.v01)
    const memo = get(w, 'designer-inspector-memo')
    expect(memo.text()).toBe('✎ 메모')
    expect(memo.attributes('disabled')).toBeDefined()
    expect(memo.attributes('title')).toBe('문서가 있는 카드에 메모를 남길 수 있습니다')
    expect(get(w, 'designer-inspector-more').attributes('aria-label')).toBe('더보기')
    expect(w.find('[data-test="designer-inspector-read-only"]').exists()).toBe(false)
    expect(get(w, 'designer-inspector-header').attributes('data-read-only')).toBe('')
  })

  it('the 읽기 전용 tag names the reason: ownership conflict, revision view', () => {
    const w = mountHeader(SELECTIONS.conflict)
    const tag = get(w, 'designer-inspector-read-only')
    expect(tag.text()).toBe('읽기 전용')
    expect(tag.attributes('data-reason')).toBe('conflict')
    expect(tag.attributes('title')).toBe('소유권 충돌 — 읽기 전용')
    expect(get(w, 'designer-inspector-header').attributes('data-read-only')).toBe('conflict')
  })

  it('⋯ → ID 복사 copies the bound document id and says so for a moment', async () => {
    vi.useFakeTimers()
    try {
      const w = mountHeader(SELECTIONS.v01)
      const item = await openMenu(w)
      expect(item.disabled).toBe(false)
      expect(item.textContent).toBe('ID 복사')
      item.click()
      await flushPromises()
      expect(writeText).toHaveBeenCalledWith('flowgate.v02.0048.0011-TR')
      expect(item.textContent).toBe('ID 복사됨')
      vi.advanceTimersByTime(1600)
      await flushPromises()
      expect(item.textContent).toBe('ID 복사')
    } finally {
      vi.useRealTimers()
    }
  })

  it('a Node without a document keeps the item, disabled', async () => {
    const w = mountHeader(SELECTIONS.graphGate)
    const item = await openMenu(w)
    expect(item.disabled).toBe(true)
    item.click()
    await flushPromises()
    expect(writeText).not.toHaveBeenCalled()
  })

  // flowgate.v02.0052 묶음 A — the v7 single status pill.
  describe('the status pill', () => {
    const pill = (w: VueWrapper<any>) => get(w, 'designer-inspector-pill')
    const label = (w: VueWrapper<any>) => get(w, 'designer-inspector-pill-label').text()
    const sub = (w: VueWrapper<any>) => w.find('[data-test="designer-inspector-pill-sub"]')

    it('shows the Canvas state once, with a document state that says something else as the sub', () => {
      const w = mountHeader(SELECTIONS.v01)
      expect(label(w)).toBe('진행 중')
      expect(sub(w).text()).toBe('· 검수 대기')
      expect(pill(w).attributes('data-tone')).toBe('info')
      expect(pill(w).attributes('data-doc-status')).toBe('pending_review')
      expect(pill(w).attributes('title')).toBe('문서 상태: 검수 대기 (pending_review) / 실행 상태: —')
    })

    it('a server state with the same meaning as the Canvas state is not repeated; a run status has plain words', () => {
      const w = mountHeader(SELECTIONS.graph)
      expect(label(w)).toBe('완료')
      expect(sub(w).exists()).toBe(false)
      expect(pill(w).attributes('data-run-status')).toBe('completed')
      expect(pill(w).attributes('title')).toBe('문서 상태: 승인됨 (approved) / 실행 상태: 완료 (completed)')
      expect(pill(w).text()).not.toContain('completed')
    })

    it('never shows an internal workflow value as text', () => {
      const base = targetOf(SELECTIONS.v01)
      const w = mount(InspectorHeader, {
        attachTo: document.body,
        props: { target: { ...base, canvasState: 'in_progress', docReviewStatus: 'wf_in_progress' } },
        global: { plugins: [i18n] },
      })
      wrapper = w
      expect(label(w)).toBe('진행 중')
      expect(sub(w).exists()).toBe(false)
      expect(pill(w).text()).not.toContain('wf_in_progress')
      expect(get(w, 'designer-inspector-doc-status').attributes('data-value')).toBe('wf_in_progress')
    })

    it('without a Canvas state the document state leads; a value without a display name stays in the tooltip', () => {
      const base = targetOf(SELECTIONS.v01)
      const w = mount(InspectorHeader, {
        attachTo: document.body,
        props: { target: { ...base, canvasState: null, docReviewStatus: 'wf_done', nodeRun: { status: 'odd', openedAt: null, finishedAt: null, resultDocId: null } } },
        global: { plugins: [i18n] },
      })
      wrapper = w
      expect(label(w)).toBe('완료')
      expect(pill(w).attributes('data-tone')).toBe('success')
      expect(sub(w).exists()).toBe(false)
      expect(pill(w).attributes('title')).toBe('문서 상태: 완료 (wf_done) / 실행 상태: odd')
    })

    // flowgate.v02.0052 T#3: Canvas `blocked` is the server's word for a rejection (canvas_mapping.py).
    it('a rejected document on a blocked Canvas state reads 반려됨, never 보류; the raw values stay', () => {
      const base = targetOf(SELECTIONS.v01)
      const w = mount(InspectorHeader, {
        attachTo: document.body,
        props: { target: { ...base, canvasState: 'blocked', docReviewStatus: 'rejected', nodeRun: null } },
        global: { plugins: [i18n] },
      })
      wrapper = w
      expect(label(w)).toBe('반려됨')
      expect(sub(w).exists()).toBe(false)
      expect(pill(w).attributes('data-tone')).toBe('danger')
      expect(pill(w).attributes('data-state')).toBe('blocked')
      // The Canvas word stays only in the hidden diagnostic span.
      expect(get(w, 'designer-inspector-state').text()).toBe('보류')
      expect(get(w, 'designer-inspector-state').attributes('hidden')).toBeDefined()
      expect(pill(w).attributes('title')).toBe('문서 상태: 반려됨 (rejected) / 실행 상태: —')
      expect(get(w, 'designer-inspector-state').attributes('data-value')).toBe('blocked')
      expect(get(w, 'designer-inspector-doc-status').attributes('data-value')).toBe('rejected')
    })

    it('a rejected Graph node run on a blocked Canvas state reads 반려됨 as well', () => {
      const base = targetOf(SELECTIONS.graph)
      const w = mount(InspectorHeader, {
        attachTo: document.body,
        props: { target: { ...base, canvasState: 'blocked', docReviewStatus: null, nodeRun: { status: 'rejected', openedAt: null, finishedAt: null, resultDocId: null } } },
        global: { plugins: [i18n] },
      })
      wrapper = w
      expect(label(w)).toBe('반려됨')
      expect(sub(w).exists()).toBe(false)
      expect(pill(w).attributes('data-tone')).toBe('danger')
      expect(pill(w).attributes('title')).toBe('문서 상태: — / 실행 상태: 반려됨 (rejected)')
    })

    it('a blocked Canvas state with no rejection behind it keeps the Canvas word (nothing is invented)', () => {
      const base = targetOf(SELECTIONS.v01)
      const w = mount(InspectorHeader, {
        attachTo: document.body,
        props: { target: { ...base, canvasState: 'blocked', docReviewStatus: null, nodeRun: null } },
        global: { plugins: [i18n] },
      })
      wrapper = w
      expect(label(w)).toBe('보류')
    })

    it('a document under review (검수 대기) is not shown as 반려됨 or 보류', () => {
      const base = targetOf(SELECTIONS.v01)
      const w = mount(InspectorHeader, {
        attachTo: document.body,
        props: { target: { ...base, canvasState: 'review', docReviewStatus: 'pending_review', nodeRun: null } },
        global: { plugins: [i18n] },
      })
      wrapper = w
      expect(label(w)).toBe('검토 중')
      expect(pill(w).text()).not.toContain('반려')
      expect(pill(w).text()).not.toContain('보류')
    })

    it('no state at all: no pill', () => {
      const base = targetOf(SELECTIONS.v01)
      const w = mount(InspectorHeader, {
        attachTo: document.body,
        props: { target: { ...base, canvasState: null, docReviewStatus: null, nodeRun: null } },
        global: { plugins: [i18n] },
      })
      wrapper = w
      expect(w.find('[data-test="designer-inspector-pill"]').exists()).toBe(false)
    })
  })

  // flowgate.v02.0052 T#4: on the 264px Inspector the 작업계획 meta badge pushed the ⋯ out of the line.
  it('a card meta part sits in its own shrinkable place before ✎ 메모; the ⋯ stays the last item', () => {
    const w = mount(InspectorHeader, {
      attachTo: document.body,
      props: { target: targetOf(SELECTIONS.v01) },
      slots: { meta: () => h('span', { 'data-test': 'meta-part' }, '작성 사람') },
      global: { plugins: [i18n] },
    })
    wrapper = w
    const meta = get(w, 'designer-inspector-meta')
    expect(meta.classes()).toContain('inspector-header-meta')
    expect(meta.get('[data-test="meta-part"]').text()).toBe('작성 사람')
    const items = Array.from(get(w, 'designer-inspector-header').element.children)
    expect(items.indexOf(meta.element)).toBeLessThan(items.indexOf(get(w, 'designer-inspector-memo').element))
    expect(items.at(-1)?.querySelector('[data-test="designer-inspector-more"]')).not.toBeNull()
    w.unmount()
    expect(mountHeader(SELECTIONS.v01).find('[data-test="designer-inspector-meta"]').exists()).toBe(false)
  })

  // flowgate.v02.0052 T#4 rev1 (R §2.2): below a 560px Inspector the header is two lines — the title
  // line and the state line (읽기 전용, pill, actions) — laid out by the frame around it. jsdom has no
  // layout (the px were measured in the real Designer), so the DOM shape and the rule are checked here.
  it('sits in its size frame; the state-line parts are header items the narrow rule can move', () => {
    const w = mount(InspectorHeader, {
      attachTo: document.body,
      props: { target: targetOf(SELECTIONS.conflict) },
      slots: { actions: () => h('button', { 'data-test': 'probe-action' }, '승인') },
      global: { plugins: [i18n] },
    })
    wrapper = w
    const header = get(w, 'designer-inspector-header').element
    const frame = header.parentElement!
    expect(frame.classList.contains('inspector-header-frame')).toBe(true)
    expect(Array.from(frame.children)).toEqual([header])
    const items = Array.from(header.children)
    for (const test of ['designer-inspector-read-only', 'designer-inspector-pill', 'designer-inspector-actions']) {
      expect(items, test).toContain(get(w, test).element)
    }
    expect(get(w, 'designer-inspector-actions').get('[data-test="probe-action"]').text()).toBe('승인')
  })

  it('keeps the narrow two-line rule: wrap, a line break, and the state line after it', () => {
    const src = readFileSync(resolve(__dirname, '../../../../src/features/designer/inspector/InspectorHeader.vue'), 'utf8')
    expect(src).toContain('container: inspector-header-frame / inline-size;')
    const at = src.indexOf('@container inspector-header-frame (max-width: 560px) {')
    expect(at).toBeGreaterThan(0)
    const rule = src.slice(at, src.indexOf('\n}\n', at))
    expect(rule).toMatch(/\.inspector-header \{[^}]*flex-wrap: wrap;[^}]*height: auto;/)
    expect(rule).toMatch(/\.inspector-header::after \{[^}]*flex: 0 0 100%;[^}]*order: 1;/)
    expect(rule).toMatch(/\.inspector-header-read-only,\s*\.inspector-header-pill,\s*\.inspector-header-actions \{\s*order: 2;/)
    // flowgate.v02.0070 T#1 rev1: the item keeping that room is the title area (the title, or its input, with the note
    // right of it) — editing asks the line for no more than the title, so the ✎ 메모 and ⋯ stay on the title line.
    expect(rule).toMatch(/\.inspector-header-title-area\.is-editing,\s*\.inspector-header-title-area \{[^}]*flex: 4 1 0;[^}]*min-width: 48px;/)
    expect(rule).not.toMatch(/min-width: 96px/)
  })

  it('follows the locale', () => {
    i18n.global.locale.value = 'en'
    const w = mountHeader(SELECTIONS.conflict)
    expect(get(w, 'designer-inspector-memo').text()).toBe('✎ Memo')
    expect(get(w, 'designer-inspector-read-only').text()).toBe('Read only')
  })
})

describe('one kind display for the Canvas card head and the header (flowgate.v02.0070 T#4, B4)', () => {
  const B_NODE: FlowGraphNode = {
    ...UNASSIGNED_REQ_NODE,
    id: 'doc:flowgate.v02.0050.0001-B',
    docType: 'B',
    document: { ...UNASSIGNED_REQ_NODE.document!, docId: 'flowgate.v02.0050.0001-B', typeCode: 'B' },
  }

  let card: VueWrapper<any> | null = null
  afterEach(() => {
    card?.unmount()
    card = null
  })

  const mountCard = (node: FlowGraphNode, title = node.title) => {
    const data: FlowGateNodeData = { kind: node.kind, docType: node.docType, title, state: node.state, selected: false }
    card = mount(FlowNodeCard, { props: { id: node.id, data }, global: { plugins: [i18n], stubs: { Handle: true } } })
    return card
  }

  it.each([
    ['R', UNASSIGNED_REQ_NODE, '요구사항', '#3b82f6'],
    ['B', B_NODE, '버그', '#ef4444'],
  ] as const)('a %s root: the card head and the header show the same name, glyph and accent', (_type, node, name, accent) => {
    const c = mountCard(node)
    const header = mountHeader(selectionOf({ ...UNASSIGNED_GRAPH, nodes: [node] }, node.id))
    expect(c.get('[data-test="designer-canvas-node-kind"]').text()).toBe(name)
    expect(get(header, 'designer-inspector-kind').text()).toBe(name)
    expect(c.get('.flow-node-icon path').attributes('d')).toBe(header.get('.inspector-header-icon path').attributes('d'))
    const cardEl = c.get('[data-test="designer-canvas-node"]')
    expect((cardEl.element as HTMLElement).style.getPropertyValue('--flow-node-accent')).toBe(accent)
    expect((get(header, 'designer-inspector-header').element as HTMLElement).style.getPropertyValue('--inspector-kind-accent')).toBe(accent)
    // The key stays the requirement kind (data-kind) on both.
    expect(cardEl.attributes('data-kind')).toBe('requirement')
    expect(get(header, 'designer-inspector-header').attributes('data-kind')).toBe('requirement')
  })

  it('a card title that only repeats the 버그 name is not shown again; its own title is', () => {
    expect(mountCard(B_NODE, '버그').find('[data-test="designer-canvas-node-title"]').exists()).toBe(false)
    card!.unmount()
    expect(mountCard(B_NODE, '로그인 실패').get('[data-test="designer-canvas-node-title"]').text()).toBe('로그인 실패')
  })

  it('flowNodeKindView: (kind, document type) -> name key, glyph, accent; only a requirement Node turns 버그', () => {
    expect(flowNodeKindView('requirement', 'B')).toMatchObject({ key: 'requirement', labelKey: 'gui.designer.inspector.header.kind_bug', accent: '#ef4444' })
    expect(flowNodeKindView('requirement', ' b ').labelKey).toBe('gui.designer.inspector.header.kind_bug')
    expect(flowNodeKindView('requirement', 'R')).toEqual({ ...flowNodeKindDisplay('requirement'), labelKey: 'gui.designer.center.node_kinds.requirement' })
    expect(flowNodeKindView('requirement', null).labelKey).toBe('gui.designer.center.node_kinds.requirement')
    expect(flowNodeKindView('requirement', 'B').icon).not.toBe(flowNodeKindDisplay('requirement').icon)
    expect(flowNodeKindView('task', 'B')).toEqual({ ...flowNodeKindDisplay('task'), labelKey: 'gui.designer.center.node_kinds.task' })
    expect(flowNodeKindView('design', 'D').labelKey).toBe('gui.designer.palette.items.basic_design')
  })
})
