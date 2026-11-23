import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// v02test.0074 card 1: the Flow attachment Host exposes toggle (and close) for the 📎 button of the chat input; it
// registers itself in `flowAttachmentUi.toggle` while mounted. The window itself is not touched by this card, so it
// is replaced by a stand-in with the two methods the Host calls.
vi.mock('@/features/designer/flowAttachments/FlowAttachmentWindow.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'FlowAttachmentWindow',
      props: ['flowId', 'projectId', 'title', 'position', 'readOnly', 'current', 'focused', 'zIndex'],
      emits: ['focus', 'close'],
      setup(_, { expose }) {
        expose({ reveal: () => {}, reload: async () => {} })
        return () => h('div', { 'data-test': 'window-standin' })
      },
    }),
  }
})
vi.mock('@/features/designer/flowAttachments/operations', () => ({
  connectReadOnly: () => () => {},
  enqueueUpload: vi.fn(),
}))

import i18n from '@shared/i18n'
import FlowAttachmentHost from '@/features/designer/flowAttachments/FlowAttachmentHost.vue'
import { flowAttachmentUi } from '@/features/designer/flowAttachments/state'

let wrapper: VueWrapper | null = null
const mountHost = (props: Record<string, unknown> = {}) => {
  wrapper = mount(FlowAttachmentHost, {
    props: { flowId: 'p.m.0001', projectId: 'p', title: 'Flow', isReadOnly: () => false, ...props },
    global: { plugins: [i18n] },
  })
  return wrapper
}
const settle = async () => {
  await nextTick()
  await nextTick()
}

beforeEach(() => {
  flowAttachmentUi.currentOpen = false
  flowAttachmentUi.toggle = null
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('attachment Host toggle', () => {
  it('registers the toggle while mounted and removes it on unmount', () => {
    mountHost()
    expect(typeof flowAttachmentUi.toggle).toBe('function')
    wrapper!.unmount()
    wrapper = null
    expect(flowAttachmentUi.toggle).toBeNull()
  })

  it('opens the window, then closes it on the next press', async () => {
    mountHost()
    expect(wrapper!.find('[data-test="window-standin"]').exists()).toBe(false)
    flowAttachmentUi.toggle!()
    await settle()
    expect(wrapper!.find('[data-test="window-standin"]').exists()).toBe(true)
    expect(flowAttachmentUi.currentOpen).toBe(true)
    flowAttachmentUi.toggle!()
    await settle()
    expect(wrapper!.find('[data-test="window-standin"]').exists()).toBe(false)
    expect(flowAttachmentUi.currentOpen).toBe(false)
  })

  it('does nothing without an open Flow', async () => {
    mountHost({ flowId: null })
    flowAttachmentUi.toggle!()
    await settle()
    expect(wrapper!.find('[data-test="window-standin"]').exists()).toBe(false)
  })

  it('exposes open / close / toggle for the shell', async () => {
    mountHost()
    const host = wrapper!.vm as unknown as { open: () => Promise<void>; close: (id: string) => void; toggle: () => void }
    await host.open()
    await settle()
    expect(flowAttachmentUi.currentOpen).toBe(true)
    host.close('p.m.0001')
    await settle()
    expect(flowAttachmentUi.currentOpen).toBe(false)
  })
})
