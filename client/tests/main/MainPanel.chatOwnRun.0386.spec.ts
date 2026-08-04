import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
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
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const GROUP_ID = 'flowgate.default.0386'
const CH_TAB = {
  id: `${GROUP_ID}.0009-CH`,
  title: 'chat',
  path: 'documents/flowgate/main/default/0386/0009-CH_document.md',
  type: 'md',
  typeCode: 'CH',
  projectId: 'flowgate',
}
const TR_TAB = {
  id: `${GROUP_ID}.0005-TR`,
  title: 'report',
  path: 'documents/flowgate/main/default/0386/0005-TR_document.md',
  type: 'md',
  typeCode: 'TR',
  projectId: 'flowgate',
}

// MainPanel resolves the group from the tab id, but DocHeader's exposed groupId is the
// fallback; a shallow stub exposing nothing would leave the run layer hidden for every tab
// and pass the CH assertions vacuously.
const DocHeaderStub = defineComponent({
  name: 'DocHeader',
  setup(_props, { expose }) {
    expose({ groupId: ref(GROUP_ID) })
    return () => h('div', { class: 'doc-header-stub' })
  },
})

function seedTabs(tabs: Record<string, unknown>[], activeTabId: unknown) {
  localStorage.setItem('flowgate.user.guest.tabs', JSON.stringify({ tabs, activeTabId }))
}

function mountPanel() {
  return mount(MainPanel, {
    attachTo: document.body,
    shallow: true,
    global: {
      plugins: [i18n],
      // AiInvokeInline is deliberately real: what is under test is whether the run surface
      // actually covers the chat, not whether a prop was handed over.
      stubs: { teleport: false, DocHeader: DocHeaderStub, AiInvokeInline: false },
    },
  })
}

function startRun(docRef: string) {
  useAiInvokeRunsStore().trackStarted({
    run_id: 'run-0386',
    group_id: GROUP_ID,
    doc_ref: docRef,
    status: 'running',
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  localStorage.clear()
  getRequest.mockReset().mockResolvedValue({ data: {} })
  postRequest.mockReset().mockResolvedValue({ data: {} })
})

afterEach(() => {
  document.body.innerHTML = ''
})

// 0386 B0001 ("AI 호출할 때 채팅도 다 가려버리면 어쩌자는거야 / 일부러 채팅은 빼놨구만"):
// 0251 B0001 took the run cover off a chat's own run and 0258 B0001 kept it for a run aimed at
// the next document. 0378's lease rework collapsed both sides into one unconditional cover.
// These pin the branch to this group so a later refactor cannot drop it unnoticed again.
describe('MainPanel chat run cover branch (0386)', () => {
  it('leaves the chat readable while the chat runs its own AI call', async () => {
    seedTabs([CH_TAB], CH_TAB.id)
    const wrapper = mountPanel()
    await flushPromises()

    startRun(CH_TAB.id)
    await flushPromises()

    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(false)
    expect(wrapper.findComponent(ConversationView).exists()).toBe(true)
  })

  it('still covers the chat when the run targets the next document', async () => {
    seedTabs([CH_TAB], CH_TAB.id)
    const wrapper = mountPanel()
    await flushPromises()

    startRun(TR_TAB.id)
    await flushPromises()

    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(true)
  })

  it('keeps the 0378 document-side lockout for a non-chat document', async () => {
    seedTabs([TR_TAB], TR_TAB.id)
    const wrapper = mountPanel()
    await flushPromises()

    startRun(TR_TAB.id)
    await flushPromises()

    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'ReviewActionBar' }).exists()).toBe(false)
  })
})
