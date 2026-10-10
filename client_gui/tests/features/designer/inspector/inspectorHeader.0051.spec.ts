/**
 * flowgate.v02.0051 — the v7 header line on every card (InspectorHeader): the 읽기 전용 tag from the
 * target, the ✎ 메모 button shown but disabled (no memo API yet), the ● state pill, and the ⋯ menu
 * with ID 복사 — enabled with a document and the clipboard, copying the bound document's id.
 * flowgate.v02.0052 묶음 A — the single status pill: one user-facing label (the Canvas state first),
 * a `· sub` only for a server state that says something else, never an internal value as text, the
 * server values in the tooltip and the data attributes.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import InspectorHeader from '@/features/designer/inspector/InspectorHeader.vue'
import { prepareInspectorI18n, SELECTIONS, targetOf } from './support/inspectorSupport'

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
  it('shows ✎ 메모 disabled with its reason, and the ⋯ button, on a card with a document', () => {
    const w = mountHeader(SELECTIONS.v01)
    const memo = get(w, 'designer-inspector-memo')
    expect(memo.text()).toBe('✎ 메모')
    expect(memo.attributes('disabled')).toBeDefined()
    expect(memo.attributes('title')).toBe('메모 — 아직 연결되지 않음')
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

  it('follows the locale', () => {
    i18n.global.locale.value = 'en'
    const w = mountHeader(SELECTIONS.conflict)
    expect(get(w, 'designer-inspector-memo').text()).toBe('✎ Memo')
    expect(get(w, 'designer-inspector-read-only').text()).toBe('Read only')
  })
})
