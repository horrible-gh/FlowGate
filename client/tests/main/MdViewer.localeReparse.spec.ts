import { flushPromises, shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia } from 'pinia'
import i18n from '@shared/i18n'
import MdViewer from '@main/components/MdViewer.vue'

// 0616 T0004 rev5 finding 2: rev4 replaced the reactive `renderedContent` computed with an
// eagerly-assigned ref, dropping its implicit dependency on the active locale (the Marked
// code-block renderer reads `t('main.md_viewer.copy_code')` at parse time). Without a
// locale-driven re-render, switching locale while a document with a code block is open left
// the generated copy-button aria-label stuck in the previous language until the next reload.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { get: vi.fn() },
  getRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const CONTENT_WITH_CODE_BLOCK = '# Title\n\n```js\nconsole.log(1)\n```\n'

function mountViewer() {
  return shallowMount(MdViewer, {
    props: {
      path: 'D:/documents/0004-D_document.md',
      docId: 'test.none.0002.0004-D',
      projectId: 'test',
    },
    global: { plugins: [i18n, createPinia()] },
  })
}

beforeEach(() => {
  getRequest.mockReset()
  getRequest.mockResolvedValue({ data: { content: CONTENT_WITH_CODE_BLOCK } })
})

afterEach(() => {
  i18n.global.locale.value = 'ko'
})

describe('MdViewer locale-driven re-render (0616 T0004 rev5 finding 2)', () => {
  it('re-renders the copy-code button aria-label when the active locale changes, without reloading content', async () => {
    i18n.global.locale.value = 'ko'
    const wrapper = mountViewer()
    await flushPromises()
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(wrapper.html()).toContain('aria-label="복사"')

    i18n.global.locale.value = 'en'
    await flushPromises()

    // No reload was triggered by the locale switch — only the rendered HTML changed.
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(wrapper.html()).toContain('aria-label="Copy"')
    expect(wrapper.html()).not.toContain('aria-label="복사"')
    wrapper.unmount()
  })

  it('does not re-render when the locale is untouched and no document is loaded', async () => {
    getRequest.mockReset()
    getRequest.mockResolvedValue({ data: { content: '' } })
    const wrapper = mountViewer()
    await flushPromises()

    // Flipping locale with nothing loaded must not throw or spuriously mark a linked source.
    i18n.global.locale.value = 'en'
    await flushPromises()
    expect((wrapper.vm as any).content).toBe('')
    wrapper.unmount()
  })
})
