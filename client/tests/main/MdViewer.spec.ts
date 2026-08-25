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

  it('removes the leading 7-field next-document header from the rendered body (T0004 2.4)', async () => {
    const header = [
      'next_type: T',
      'next_type_detail: 작업지시',
      'project: flowgate',
      'module: default',
      'group: 0458',
      'title: 검수 행 식별 기반 중복 반려 차단과 재진입 멱등 회귀를 아주 길게 늘려 wrapping도 함께 확인하는 한글 제목',
      'target_id: B0001',
    ].join('\n')
    const source = `${header}\n\n일반 문단은 줄바꿈이 있어도 하나로 이어져 보입니다.\n두 번째 줄입니다.`

    const wrapper = mount(MdViewer, {
      props: { path: null, contentOverride: source },
      global: { plugins: [i18n, createPinia()] },
    })
    await wrapper.vm.$nextTick()

    const contentEl = wrapper.find('.md-viewer__content').element as HTMLElement
    // Not a paragraph, not a code box, not anywhere: none of the seven fields is on
    // screen. rev1-rev4 put them in a <pre> and were rejected for exactly that.
    expect(contentEl.querySelector('pre')).toBeNull()
    for (const field of header.split('\n')) {
      expect(contentEl.textContent).not.toContain(field)
    }
    expect(contentEl.textContent).not.toContain('next_type')
    expect(contentEl.textContent).not.toContain('target_id')

    // Ordinary prose keeps its existing soft-line-break paragraph behavior — the
    // two lines below the header are still joined into one <p>, untouched.
    const paragraphs = Array.from(contentEl.querySelectorAll('p')).map((p) => p.textContent)
    expect(
      paragraphs.some((t) => t?.includes('일반 문단은') && t?.includes('두 번째 줄입니다.')),
    ).toBe(true)
  })

  it('copies the raw header text unchanged — the removal is display-only', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    const header = [
      'next_type: T',
      'next_type_detail: 작업지시',
      'project: flowgate',
      'module: default',
      'group: 0458',
      'title: 검수 행 식별 기반 중복 반려 차단과 재진입 멱등 회귀',
      'target_id: B0001',
    ].join('\n')
    const source = `${header}\n\nBody.`
    const wrapper = mount(MdViewer, {
      props: { path: null, contentOverride: source },
      global: { plugins: [i18n, createPinia()] },
    })

    await wrapper.find('.md-copy-btn--main').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenNthCalledWith(1, source)

    await wrapper.find('.md-copy-btn--header').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenNthCalledWith(2, source)
  })

  it('shows nothing of the header of the rejected flowgate.test document (R0001 rev5)', async () => {
    // Byte-for-byte content of test.test.0010.0001-R on http://flowgate.test/ —
    // the document the header rejections were filed against. CRLF, a YAML
    // frontmatter block, one blank line, then the seven fields. rev3 rendered this
    // as a single run-together <p> (stripFrontmatter() leaves the blank line, so
    // the leading-header gate never matched, and the CRLF lines never matched the
    // per-line regex either); rev4 rendered it as a code box. Both were rejected —
    // the header must not be on screen at all.
    const fields = [
      'next_type: R',
      'next_type_detail: 요건정의',
      'project: flowgate',
      'module: default',
      'group: 0010',
      'title: 테스트',
      'target_id: R0001',
    ]
    const source = [
      '---',
      'title: 0408 TR0021 rev1 provider tab verify',
      'type: R',
      'doc_id: test.test.0010.0001-R',
      '---',
      '',
      ...fields,
    ].join('\r\n')

    const wrapper = mount(MdViewer, {
      props: { path: null, contentOverride: source },
      global: { plugins: [i18n, createPinia()] },
    })
    await wrapper.vm.$nextTick()

    const contentEl = wrapper.find('.md-viewer__content').element as HTMLElement
    // The document is nothing but frontmatter + header, so the rendered body is
    // empty: no <pre>, no <p>, and none of the seven fields anywhere.
    expect(contentEl.querySelector('pre')).toBeNull()
    expect(contentEl.querySelector('p')).toBeNull()
    expect((contentEl.textContent || '').trim()).toBe('')
    for (const field of fields) {
      expect(contentEl.textContent).not.toContain(field)
    }
  })

  it('keeps both copy buttons byte-exact for that document (display-only removal)', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    const source = [
      '---',
      'title: 0408 TR0021 rev1 provider tab verify',
      'type: R',
      'doc_id: test.test.0010.0001-R',
      '---',
      '',
      'next_type: R',
      'next_type_detail: 요건정의',
      'project: flowgate',
      'module: default',
      'group: 0010',
      'title: 테스트',
      'target_id: R0001',
    ].join('\r\n')

    const wrapper = mount(MdViewer, {
      props: { path: null, contentOverride: source },
      global: { plugins: [i18n, createPinia()] },
    })

    await wrapper.find('.md-copy-btn--main').trigger('click')
    await flushPromises()
    // "Copy Markdown" = frontmatter stripped, everything else untouched — the
    // seven header fields are still part of what the user copies.
    expect(writeText).toHaveBeenNthCalledWith(1, source.slice(source.indexOf('---\r\n', 4) + 5))
    expect(writeText.mock.calls[0][0]).toContain('next_type: R')
    expect(writeText.mock.calls[0][0]).toContain('target_id: R0001')

    await wrapper.find('.md-copy-btn--header').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenNthCalledWith(2, source)
  })

  it('removes a CRLF header that has no frontmatter above it', async () => {
    const fields = [
      'next_type: T',
      'next_type_detail: 작업지시',
      'project: flowgate',
      'module: default',
      'group: 0458',
      'title: 검수 행 식별 기반 중복 반려 차단과 재진입 멱등 회귀',
      'target_id: B0001',
    ]
    const wrapper = mount(MdViewer, {
      props: { path: null, contentOverride: fields.join('\r\n') + '\r\n\r\n산문 한 줄.' },
      global: { plugins: [i18n, createPinia()] },
    })
    await wrapper.vm.$nextTick()

    const contentEl = wrapper.find('.md-viewer__content').element as HTMLElement
    expect(contentEl.querySelector('pre')).toBeNull()
    for (const field of fields) {
      expect(contentEl.textContent).not.toContain(field)
    }
    // The prose below the header is all that is left, and it is intact.
    expect(contentEl.textContent).toContain('산문 한 줄.')
  })

  it('leaves an already-multiline non-header document untouched', async () => {
    const source = '# Title\n\nFirst line.\nSecond line joins it visually.'
    const wrapper = mount(MdViewer, {
      props: { path: null, contentOverride: source },
      global: { plugins: [i18n, createPinia()] },
    })
    await wrapper.vm.$nextTick()

    const contentEl = wrapper.find('.md-viewer__content').element as HTMLElement
    expect(contentEl.querySelector('pre')).toBeNull()
  })

  it('keeps copy and scrolling available but hides regeneration in read-only mode', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    const readable = mount(MdViewer, {
      props: { path: null, contentOverride: '# Read only', readOnly: true },
      global: { plugins: [i18n, createPinia()] },
    })
    const content = readable.find('.md-viewer__content').element as HTMLElement
    content.scrollTop = 25
    await readable.find('.md-copy-btn--main').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith('# Read only')
    expect(content.scrollTop).toBe(25)

    getRequest.mockRejectedValueOnce({ response: { status: 404 } })
    const missing = mount(MdViewer, {
      props: { path: null, docId: 'flowgate.default.0398.0001-R', readOnly: true },
      global: { plugins: [i18n, createPinia()] },
    })
    await flushPromises()
    expect(missing.find('.md-viewer__empty').exists()).toBe(true)
    expect(missing.find('.md-viewer__regen-btn').exists()).toBe(false)
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
