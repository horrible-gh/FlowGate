import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyToClipboard } from '@/core/interaction/clipboard'

const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
const execDescriptor = Object.getOwnPropertyDescriptor(document, 'execCommand')

function clipboard(value: { writeText: (text: string) => Promise<void> } | undefined) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value })
}

function execCopy(fn: () => boolean) {
  Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn(fn) })
}

afterEach(() => {
  if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor)
  else Reflect.deleteProperty(navigator, 'clipboard')
  if (execDescriptor) Object.defineProperty(document, 'execCommand', execDescriptor)
  else Reflect.deleteProperty(document, 'execCommand')
  document.body.innerHTML = ''
})

describe('copyToClipboard', () => {
  it('uses the Clipboard API when available', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    clipboard({ writeText })
    execCopy(() => true)
    expect(await copyToClipboard('command')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('command')
    expect(document.execCommand).not.toHaveBeenCalled()
  })

  it.each(['missing', 'rejected'])('copies inside the supplied modal after API is %s', async (state) => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    clipboard(state === 'missing' ? undefined : { writeText })
    const modal = document.createElement('div')
    document.body.appendChild(modal)
    execCopy(() => {
      const area = document.activeElement as HTMLTextAreaElement
      expect(area.parentElement).toBe(modal)
      expect(area.value).toBe('command')
      expect(area.selectionStart).toBe(0)
      expect(area.selectionEnd).toBe(7)
      return true
    })
    expect(await copyToClipboard('command', modal)).toBe(true)
    expect(modal.childElementCount).toBe(0)
    expect(document.execCommand).toHaveBeenCalledWith('copy')
    if (state === 'rejected') expect(writeText).toHaveBeenCalledWith('command')
  })

  it('returns false after the legacy command rejects', async () => {
    clipboard(undefined)
    execCopy(() => false)
    expect(await copyToClipboard('command')).toBe(false)
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('returns false after the legacy command throws', async () => {
    clipboard(undefined)
    execCopy(() => { throw new Error('denied') })
    expect(await copyToClipboard('command')).toBe(false)
    expect(document.querySelector('textarea')).toBeNull()
  })
})
