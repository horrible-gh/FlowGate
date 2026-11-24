import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import ConversationComposer from '@/features/conversation/ConversationComposer.vue'

const net = vi.hoisted(() => ({ upload: vi.fn(), remove: vi.fn(), blob: vi.fn() }))
vi.mock('@/features/conversation/api', async (original) => ({
  ...(await original<typeof import('@/features/conversation/api')>()),
  uploadMessageImage: net.upload,
  deleteMessageImage: net.remove,
  fetchMessageImageBlob: net.blob,
}))

registerAppMessages(i18n)

// flowgate.v02test.0074.0013-T (TR0014): a file dropped on the chat input is never opened by the browser, also while
// a message is being sent or the input is locked. Nothing is attached then; drags without files keep their default.
let wrappers: VueWrapper[] = []
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers = []; vi.clearAllMocks() })

function mountComposer(props: Record<string, unknown> = {}) {
  const wrapper = mount(ConversationComposer, {
    props: {
      chatId: 'p.m.0001.0002-CH', projectId: 'p', groupId: 'p.m.0001', disabled: false, settings: DEFAULT_CHAT_SETTINGS,
      savingSettings: false, pendingSourceMode: null, providers: [], selectedProviderId: '', providersStatus: 'ready',
      hasProviders: false, sending: false, running: false, runStarting: false, cancelling: false, copyingMention: false,
      manualCopyText: null, onSend: vi.fn(async () => true), ...props,
    },
    attachTo: document.body,
    global: { plugins: [i18n], stubs: { ComposerModelPicker: true, ComposerSourceModes: true, ComposerSettings: true, ComposerToolsMenu: true } },
  })
  wrappers.push(wrapper)
  return wrapper
}

function fire(wrapper: VueWrapper, type: 'dragover' | 'drop', types: string[] = ['Files']) {
  const transfer = { types, files: [new File([new Uint8Array([1, 2, 3])], 'shot.png', { type: 'image/png' })], dropEffect: 'copy' }
  const event = new Event(type, { bubbles: true, cancelable: true })
  Object.defineProperty(event, 'dataTransfer', { value: transfer })
  wrapper.get('[data-test="conv-composer"]').element.dispatchEvent(event)
  return { event, transfer }
}

describe('chat input file drop never reaches the browser (0074 TR0014)', () => {
  for (const [name, props] of [['sending', { sending: true }], ['disabled', { disabled: true }], ['read-only', { readOnly: true }]] as const) {
    it(`while ${name}: dragover / drop are prevented, dropEffect none, nothing is attached`, async () => {
      const w = mountComposer(props)
      const over = fire(w, 'dragover')
      expect(over.event.defaultPrevented).toBe(true)
      expect(over.transfer.dropEffect).toBe('none')
      await flushPromises()
      expect(w.get('[data-test="conv-composer"]').classes()).not.toContain('is-dragging')
      expect(fire(w, 'drop').event.defaultPrevented).toBe(true)
      await flushPromises()
      expect(net.upload).not.toHaveBeenCalled()
      expect(w.findAll('[data-test="conv-image"]')).toHaveLength(0)
    })
  }

  it('when the input can take files, dragover still shows the drop state and copies', async () => {
    const w = mountComposer()
    const over = fire(w, 'dragover')
    expect(over.event.defaultPrevented).toBe(true)
    expect(over.transfer.dropEffect).toBe('copy')
    await flushPromises()
    expect(w.get('[data-test="conv-composer"]').classes()).toContain('is-dragging')
  })

  it('drags without files (text, quick message order) keep their default', () => {
    const w = mountComposer({ sending: true })
    expect(fire(w, 'dragover', ['text/plain']).event.defaultPrevented).toBe(false)
    expect(fire(w, 'drop', ['text/plain']).event.defaultPrevented).toBe(false)
  })
})
