import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AiCommandInfoModal from '@/features/ai/providers/AiCommandInfoModal.vue'
import type { ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

const provider: ProviderItem = {
  id: 'cli-1', name: 'Claude', kind: 'claude', exec_type: 'cli', enabled: true, cli_command: '',
}
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
const execDescriptor = Object.getOwnPropertyDescriptor(document, 'execCommand')

function setClipboard(value: { writeText: (text: string) => Promise<void> } | undefined) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value })
}
function setExecCommand(fn: () => boolean) {
  Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn(fn) })
}
function mountModal(item: ProviderItem = provider) {
  return mount(AiCommandInfoModal, {
    props: { provider: item },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
}
async function clickCopy() {
  const button = document.querySelector('.code-copy') as HTMLButtonElement
  button.focus()
  button.click()
  await flushPromises()
}

describe('AiCommandInfoModal', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'ko'
  })
  afterEach(() => {
    if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor)
    else Reflect.deleteProperty(navigator, 'clipboard')
    if (execDescriptor) Object.defineProperty(document, 'execCommand', execDescriptor)
    else Reflect.deleteProperty(document, 'execCommand')
    document.body.innerHTML = ''
    vi.useRealTimers()
  })

  it('shows and copies the displayed default command when cli_command is empty', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    const wrapper = mountModal()
    expect(document.querySelector('.info-v')?.textContent).toBe('(기본 커맨드)')
    expect(document.querySelector('.code-copy')).not.toBeNull()
    await clickCopy()
    expect(writeText).toHaveBeenCalledWith('(기본 커맨드)')
    expect(wrapper.emitted('toast')?.[0]).toEqual(['커맨드를 클립보드에 복사했습니다.', 'success'])
    expect(document.querySelector('.code-copy')?.classList.contains('is-copied')).toBe(true)
    wrapper.unmount()
  })

  it.each(['missing', 'rejected'])('uses a modal-contained fallback when Clipboard API is %s', async (state) => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    setClipboard(state === 'missing' ? undefined : { writeText })
    setExecCommand(() => {
      const area = document.activeElement as HTMLTextAreaElement
      expect(area.parentElement).toBe(document.querySelector('.ui-modal-surface'))
      expect(area.value).toBe('claude run')
      return true
    })
    const wrapper = mountModal({ ...provider, cli_command: 'claude run' })
    await clickCopy()
    expect(document.execCommand).toHaveBeenCalledWith('copy')
    expect(wrapper.emitted('toast')?.[0]?.[1]).toBe('success')
    expect(document.querySelector('.ui-modal-surface')?.contains(document.activeElement)).toBe(true)
    if (state === 'rejected') expect(writeText).toHaveBeenCalledWith('claude run')
    wrapper.unmount()
  })

  it('offers a selected, focusable manual copy field after both paths fail', async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('denied')) })
    setExecCommand(() => false)
    const wrapper = mountModal({ ...provider, cli_command: 'claude run' })
    await clickCopy()
    const area = document.querySelector('.manual-copy') as HTMLTextAreaElement
    expect(area).toBeTruthy()
    expect(area.readOnly).toBe(true)
    expect(area.value).toBe('claude run')
    expect(document.activeElement).toBe(area)
    expect(area.selectionStart).toBe(0)
    expect(area.selectionEnd).toBe(area.value.length)
    expect(wrapper.emitted('toast')?.[0]?.[1]).toBe('danger')
    expect(document.querySelector('.code-copy')?.classList.contains('is-copied')).toBe(false)
    wrapper.unmount()
  })
})
