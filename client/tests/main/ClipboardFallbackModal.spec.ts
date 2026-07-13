import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import ClipboardFallbackModal from '@main/components/ClipboardFallbackModal.vue'
import {
  closeClipboardFallback,
  openClipboardFallback,
  useClipboardFallback,
} from '@main/composables/useClipboardFallback'
import { consumeLastFailedCopyText, copyToClipboard } from '@main/utils/clipboard'

// B0001 / group 0221: on this HTTP LAN deploy navigator.clipboard does not exist, so a copy
// that fails after the producer round-trip has no automatic recovery — the manual-copy
// fallback modal is the reliable path. These tests pin the composable contract and the
// modal's re-copy behavior.

const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

const origClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
const origExecCommand = Object.getOwnPropertyDescriptor(document, 'execCommand')

function setClipboard(value: any) {
  Object.defineProperty(navigator, 'clipboard', { value, configurable: true })
}
function setExecCommand(fn: any) {
  Object.defineProperty(document, 'execCommand', { value: fn, configurable: true })
}

// The modal teleports to document.body, so it must be unmounted (never wiped via
// innerHTML) or Vue's next patch dereferences removed DOM nodes.
let wrapper: ReturnType<typeof mount> | null = null
function mountModal() {
  wrapper = mount(ClipboardFallbackModal, { global: { plugins: [i18n] } })
}

beforeEach(() => {
  showToast.mockReset()
  setClipboard(undefined)
})

afterEach(() => {
  closeClipboardFallback()
  wrapper?.unmount()
  wrapper = null
  consumeLastFailedCopyText()
  if (origClipboard) Object.defineProperty(navigator, 'clipboard', origClipboard)
  else Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
  if (origExecCommand) Object.defineProperty(document, 'execCommand', origExecCommand)
  else delete (document as any).execCommand
  vi.restoreAllMocks()
})

describe('useClipboardFallback', () => {
  it('opens with an explicit text and closes back to empty', () => {
    const { state } = useClipboardFallback()
    expect(openClipboardFallback('mention body')).toBe(true)
    expect(state.visible).toBe(true)
    expect(state.text).toBe('mention body')

    closeClipboardFallback()
    expect(state.visible).toBe(false)
    expect(state.text).toBe('')
  })

  it('picks up the text of the last failed copy when called without one', async () => {
    vi.spyOn(window, 'focus').mockImplementation(() => {})
    setExecCommand(vi.fn().mockReturnValue(false))
    expect(await copyToClipboard('failed mention')).toBe(false)

    const { state } = useClipboardFallback()
    expect(openClipboardFallback()).toBe(true)
    expect(state.text).toBe('failed mention')
  })

  it('reports false when no text is known (caller falls back to a toast)', () => {
    expect(openClipboardFallback()).toBe(false)
    expect(useClipboardFallback().state.visible).toBe(false)
  })
})

describe('ClipboardFallbackModal', () => {
  it('shows the pending text and closes with a success toast when the re-copy lands', async () => {
    vi.spyOn(window, 'focus').mockImplementation(() => {})
    // The modal's copy button carries a FRESH activation, so execCommand succeeds here.
    setExecCommand(vi.fn().mockReturnValue(true))
    mountModal()

    openClipboardFallback('manual copy me')
    await flushPromises()

    const textarea = document.body.querySelector('.cfb-text') as HTMLTextAreaElement
    expect(textarea).not.toBeNull()
    expect(textarea.value).toBe('manual copy me')

    const copyBtn = document.body.querySelector('.modal-ft .btn-primary') as HTMLButtonElement
    copyBtn.click()
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.clipboard_fallback.toast_copied'),
      'success',
    )
    expect(useClipboardFallback().state.visible).toBe(false)
  })

  it('stays open with a warning toast when even the re-copy fails', async () => {
    vi.spyOn(window, 'focus').mockImplementation(() => {})
    setExecCommand(vi.fn().mockReturnValue(false))
    mountModal()

    openClipboardFallback('still failing')
    await flushPromises()

    const copyBtn = document.body.querySelector('.modal-ft .btn-primary') as HTMLButtonElement
    copyBtn.click()
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.clipboard_fallback.toast_copy_failed'),
      'warning',
    )
    expect(useClipboardFallback().state.visible).toBe(true)
  })
})
