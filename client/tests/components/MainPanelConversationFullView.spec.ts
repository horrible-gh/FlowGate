import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import i18n from '@shared/i18n'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  getRequest: (...a: unknown[]) => getRequest(...a),
  postRequest: (...a: unknown[]) => postRequest(...a),
  putRequest: (...a: unknown[]) => postRequest(...a),
  deleteRequest: (...a: unknown[]) => postRequest(...a),
}))

import MainPanel from '@main/components/MainPanel.vue'
import ConversationView from '@main/components/ConversationView.vue'
import AiInvokeInline from '@main/components/AiInvokeInline.vue'

const GROUP_ID = 'flowgate.default.0085'

const CH_TAB = {
  id: 'flowgate.default.0085.0009-CH',
  title: 'chat',
  path: 'documents/flowgate/main/default/0085/0009-CH_document.md',
  type: 'md',
  typeCode: 'CH',
  projectId: 'flowgate',
}

const TR_TAB = {
  id: 'flowgate.default.0085.0010-TR',
  title: 'report',
  path: 'documents/flowgate/main/default/0085/0010-TR_document.md',
  type: 'md',
  typeCode: 'TR',
  projectId: 'flowgate',
}

// The AI-run layer only mounts once a group is resolved, and MainPanel resolves it from
// DocHeader's exposed groupId. A shallow stub exposes nothing, which would leave the layer
// hidden for every tab and make the CH assertion below pass vacuously.
const DocHeaderStub = defineComponent({
  name: 'DocHeader',
  setup(_props, { expose }) {
    expose({ groupId: ref(GROUP_ID) })
    return () => h('div', { class: 'doc-header-stub' })
  },
})

function seedTabs(tab: Record<string, unknown> = CH_TAB) {
  localStorage.setItem(
    'flowgate.user.guest.tabs',
    JSON.stringify({ tabs: [tab], activeTabId: tab.id }),
  )
}

function mountPanel() {
  return mount(MainPanel, {
    attachTo: document.body,
    shallow: true,
    global: {
      plugins: [i18n, createPinia()],
      stubs: { teleport: false, DocHeader: DocHeaderStub },
    },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  localStorage.clear()
  getRequest.mockReset().mockResolvedValue({ data: {} })
  postRequest.mockReset().mockResolvedValue({ data: {} })
  seedTabs()
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('MainPanel CH stays inline', () => {
  it('renders exactly one ConversationView inside the chat card', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.findAllComponents(ConversationView)).toHaveLength(1)
    const conversation = document.body.querySelector('conversation-view-stub')
    expect(conversation).not.toBeNull()
    expect(conversation!.parentElement!.className).toContain('conv-card-bd')
  })

  it('does not offer a full-screen action for chat', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('.conv-card .card-actions').exists()).toBe(false)
    expect(wrapper.find('.conv-card [aria-label]').exists()).toBe(false)
  })

  it('guards the full-view handler from opening a modal for CH', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    ;(wrapper.vm as any).openFullView(CH_TAB)
    await flushPromises()

    expect(document.body.querySelector('.document-modal')).toBeNull()
    expect(document.body.querySelector('conversation-view-stub')!.parentElement!.className)
      .toContain('conv-card-bd')
  })

  // 0251 B0001: the chat's own AI call is a group-scoped run, so the group's AI-run layer
  // (inset:0 over .doc-main) used to cover the whole chat document the moment a message was
  // sent. Chat progress belongs on the send button.
  it('never covers the chat with the group AI-run layer', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.findComponent(AiInvokeInline).exists()).toBe(false)
  })

  it('still shows the AI-run layer on a non-chat document', async () => {
    seedTabs(TR_TAB)
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.findComponent(AiInvokeInline).exists()).toBe(true)
  })
})
