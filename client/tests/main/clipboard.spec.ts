import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ClipboardAbort, copyToClipboard, copyToClipboardDeferred } from '@main/utils/clipboard'

// B0001 (group 0133): the mention-copy paths intermittently "didn't copy / truncated / blanked".
// Root cause was an awaited token round-trip between the click and the clipboard write, plus a
// helper that swallowed the failure and reported success. These tests pin the two fixes:
//  • copyToClipboard reports honestly (retry-on-focus, false when nothing was written)
//  • copyToClipboardDeferred preserves activation via ClipboardItem(promise) and aborts quietly

const origClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
const origClipboardItem = (globalThis as any).ClipboardItem

function setClipboard(value: any) {
  Object.defineProperty(navigator, 'clipboard', { value, configurable: true })
}

afterEach(() => {
  if (origClipboard) Object.defineProperty(navigator, 'clipboard', origClipboard)
  else Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
  ;(globalThis as any).ClipboardItem = origClipboardItem
  vi.restoreAllMocks()
})

describe('copyToClipboard (honest write)', () => {
  it('returns true when writeText resolves', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    expect(await copyToClipboard('hello')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('hello')
  })

  it('re-focuses and retries once when the first write rejects', async () => {
    const writeText = vi
      .fn()
      .mockRejectedValueOnce(new DOMException('Document is not focused', 'NotAllowedError'))
      .mockResolvedValueOnce(undefined)
    setClipboard({ writeText })
    const focus = vi.spyOn(window, 'focus').mockImplementation(() => {})

    expect(await copyToClipboard('retry-me')).toBe(true)
    expect(writeText).toHaveBeenCalledTimes(2)
    expect(focus).toHaveBeenCalled()
  })

  it('returns false (not a false success) when every path fails', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    setClipboard({ writeText })
    vi.spyOn(window, 'focus').mockImplementation(() => {})
    // execCommand absent / returns false in jsdom → no fallback success
    ;(document as any).execCommand = vi.fn().mockReturnValue(false)

    expect(await copyToClipboard('nope')).toBe(false)
  })
})

describe('copyToClipboardDeferred (activation-preserving)', () => {
  it('builds a ClipboardItem with a text promise and writes it (no await before write)', async () => {
    let writtenSize = -1
    let writtenType = ''
    const write = vi.fn(async (items: any[]) => {
      const blob: Blob = await items[0].store['text/plain']
      writtenSize = blob.size
      writtenType = blob.type
    })
    setClipboard({ write })
    // Minimal ClipboardItem stand-in that retains the promised payload for inspection.
    ;(globalThis as any).ClipboardItem = class {
      store: Record<string, Promise<Blob>>
      constructor(store: Record<string, Promise<Blob>>) {
        this.store = store
      }
    }

    let producerStarted = false
    const ok = await copyToClipboardDeferred(async () => {
      producerStarted = true
      return 'deferred-mention'
    })

    expect(ok).toBe(true)
    expect(producerStarted).toBe(true)
    expect(write).toHaveBeenCalledTimes(1)
    // The promised payload carries the produced text as a text/plain blob.
    expect(writtenSize).toBe(new Blob(['deferred-mention']).size)
    expect(writtenType).toBe('text/plain')
  })

  it('falls back to await+writeText when ClipboardItem is unavailable', async () => {
    ;(globalThis as any).ClipboardItem = undefined
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })

    const ok = await copyToClipboardDeferred(async () => 'legacy-mention')
    expect(ok).toBe(true)
    expect(writeText).toHaveBeenCalledWith('legacy-mention')
  })

  it('aborts quietly (false, no write) when the producer throws ClipboardAbort', async () => {
    ;(globalThis as any).ClipboardItem = undefined
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })

    const ok = await copyToClipboardDeferred(async () => {
      throw new ClipboardAbort()
    })
    expect(ok).toBe(false)
    expect(writeText).not.toHaveBeenCalled()
  })
})
