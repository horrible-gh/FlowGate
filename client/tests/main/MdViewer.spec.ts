import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia } from 'pinia'
import i18n from '@shared/i18n'
import MdViewer from '@main/components/MdViewer.vue'
import { closeClipboardFallback, useClipboardFallback } from '@main/composables/useClipboardFallback'

const { getRequest, apiGet, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  apiGet: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  getRequest,
  default: {
    get: apiGet,
  },
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
const originalExecCommand = Object.getOwnPropertyDescriptor(document, 'execCommand')

function setClipboard(value: { writeText: ReturnType<typeof vi.fn> } | undefined) {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value,
  })
}

function setExecCommand(fn: ReturnType<typeof vi.fn>) {
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    value: fn,
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  apiGet.mockReset()
  showToast.mockReset()
  setClipboard({ writeText: vi.fn().mockResolvedValue(undefined) })
  setExecCommand(vi.fn().mockReturnValue(true))
})

afterEach(() => {
  vi.useRealTimers()
  if (originalClipboard) {
    Object.defineProperty(navigator, 'clipboard', originalClipboard)
  } else {
    delete (navigator as Navigator & { clipboard?: Clipboard }).clipboard
  }
  if (originalExecCommand) {
    Object.defineProperty(document, 'execCommand', originalExecCommand)
  } else {
    delete (document as Document & { execCommand?: (commandId: string) => boolean }).execCommand
  }
})

describe('MdViewer', () => {
  it('keeps empty linked files out of the no-md state', async () => {
    apiGet.mockResolvedValue({ data: '' })

    const wrapper = mount(MdViewer, {
      props: {
        path: 'docs/empty.md',
        projectId: 'proj-t509',
      },
      global: {
        plugins: [i18n, createPinia()],
      },
    })

    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.md-viewer__toolbar').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('No linked MD file found.')
  })

  it('shows no-md state only on true 404 missing files', async () => {
    apiGet.mockRejectedValue({ response: { status: 404 } })

    const wrapper = mount(MdViewer, {
      props: {
        path: 'docs/missing.md',
        projectId: 'proj-t509',
      },
      global: {
        plugins: [i18n, createPinia()],
      },
    })

    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.md-viewer__error').exists()).toBe(false)
    expect(wrapper.text()).toContain('연결된 MD 파일이 없습니다.')
  })

  it('copies markdown without frontmatter and copies the full source from the header button', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    const source = '---\ntitle: Test\n---\n# Body'
    const wrapper = mount(MdViewer, {
      props: {
        path: null,
        contentOverride: source,
      },
      global: {
        plugins: [i18n, createPinia()],
      },
    })

    await wrapper.find('.md-copy-btn--main').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenNthCalledWith(1, '# Body')

    await wrapper.find('.md-copy-btn--header').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenNthCalledWith(2, source)
    expect(showToast).not.toHaveBeenCalled()
  })

  it('falls back to execCommand when the Clipboard API is unavailable', async () => {
    setClipboard(undefined)
    const execCommand = vi.fn().mockImplementation(() => {
      expect(document.querySelector('textarea')?.value).toBe('# LAN HTTP')
      return true
    })
    setExecCommand(execCommand)
    const wrapper = mount(MdViewer, {
      props: {
        path: null,
        contentOverride: '# LAN HTTP',
      },
      global: {
        plugins: [i18n, createPinia()],
      },
    })

    await wrapper.find('.md-copy-btn--main').trigger('click')
    await flushPromises()

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(document.querySelector('textarea')).toBeNull()
    expect(wrapper.find('.md-copy-btn--main').classes()).toContain('md-copy-btn--copied')
    expect(showToast).not.toHaveBeenCalled()
  })

  it('falls back after writeText rejects and marks a code block as copied', async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException('Denied', 'NotAllowedError'))
    setClipboard({ writeText })
    const execCommand = vi.fn().mockReturnValue(true)
    setExecCommand(execCommand)
    const wrapper = mount(MdViewer, {
      props: {
        path: null,
        contentOverride: '```ts\nconst answer = 42\n```',
      },
      global: {
        plugins: [i18n, createPinia()],
      },
    })

    const codeCopyButton = wrapper.find('.code-copy-btn')
    await codeCopyButton.trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('const answer = 42')
    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(codeCopyButton.classes()).toContain('code-copy-btn--copied')
    expect(codeCopyButton.text()).toBe('복사됨!')
    expect(showToast).not.toHaveBeenCalled()
  })

  it('opens the manual-copy fallback modal when both copy methods fail (B0001 / 0221)', async () => {
    setClipboard(undefined)
    setExecCommand(vi.fn().mockReturnValue(false))
    const wrapper = mount(MdViewer, {
      props: {
        path: null,
        contentOverride: '```\nfailed copy\n```',
      },
      global: {
        plugins: [i18n, createPinia()],
      },
    })

    const { state } = useClipboardFallback()
    try {
      await wrapper.find('.code-copy-btn').trigger('click')
      await flushPromises()

      // The failed text is carried into the fallback modal instead of a dead-end toast.
      expect(state.visible).toBe(true)
      expect(state.text.trim()).toBe('failed copy')
      expect(showToast).not.toHaveBeenCalled()
      expect(wrapper.find('.code-copy-btn').classes()).not.toContain('code-copy-btn--copied')
    } finally {
      closeClipboardFallback()
    }
  })
})
