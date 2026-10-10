/**
 * flowgate.v02.0051 — the v7 header line on every card (InspectorHeader): the 읽기 전용 tag from the
 * target, the ✎ 메모 button shown but disabled (no memo API yet), the ● state pill, and the ⋯ menu
 * with ID 복사 — enabled with a document and the clipboard, copying the bound document's id.
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

  it('follows the locale', () => {
    i18n.global.locale.value = 'en'
    const w = mountHeader(SELECTIONS.conflict)
    expect(get(w, 'designer-inspector-memo').text()).toBe('✎ Memo')
    expect(get(w, 'designer-inspector-read-only').text()).toBe('Read only')
  })
})
