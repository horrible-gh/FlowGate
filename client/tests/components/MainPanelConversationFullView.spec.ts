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
import { useTabsStore } from '@main/stores/tabs'

const GROUP_ID = 'flowgate.default.0085'
const NEXT_DOC_ID = 'flowgate.default.0085.0011-R'

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

function seedTabs(tabs: Record<string, unknown>[] = [CH_TAB], activeTabId = tabs[0].id) {
  localStorage.setItem(
    'flowgate.user.guest.tabs',
    JSON.stringify({ tabs, activeTabId }),
  )
}

// AiInvokeInline is deliberately NOT stubbed: the point of these tests is whether the layer
// actually covers the chat, not whether a prop was handed over. Its own children stay stubbed.
function mountPanel() {
  return mount(MainPanel, {
    attachTo: document.body,
    shallow: true,
    global: {
      plugins: [i18n],
      stubs: { teleport: false, DocHeader: DocHeaderStub, AiInvokeInline: false },
    },
  })
}

function startRun(docRef: string, runId = 'run-1') {
  useAiInvokeRunsStore().trackStarted({
    run_id: runId,
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
  //
  // The suppression key is only correct if it equals the docRef the chat really starts its run
  // with. That seam lives across two components — MainPanel passes tab.id, ConversationView
  // mounts on :doc-id="tab.id" and posts doc_ref: props.docId — so the run below is keyed off
  // the mounted ConversationView's own docId rather than a hand-written constant.
  it('never covers the chat with a run this chat started', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const chatDocId = wrapper.findComponent(ConversationView).props('docId') as string
    expect(chatDocId).toBe(CH_TAB.id)

    startRun(chatDocId)
    await flushPromises()

    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(false)
  })

  // 0258 B0001: a run aimed at another document is a next-document transition, and the chat
  // must be covered for it exactly like any other doc type.
  it('covers the chat with a run that targets the next document', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    startRun(NEXT_DOC_ID)
    await flushPromises()

    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(true)
  })

  it('still shows the AI-run layer on a non-chat document', async () => {
    seedTabs([TR_TAB])
    const wrapper = mountPanel()
    await flushPromises()

    startRun(NEXT_DOC_ID)
    await flushPromises()

    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(true)
  })

  // NR0003 required regression 4: the cover is released once the run ends and is dismissed,
  // handing the chat back to the user. A finished run keeps its result panel until dismissed.
  it('releases the chat once a next-document run finishes and is dismissed', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    startRun(NEXT_DOC_ID)
    await flushPromises()
    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(true)

    const store = useAiInvokeRunsStore()
    store.trackFinished({
      run_id: 'run-1',
      group_id: GROUP_ID,
      doc_ref: NEXT_DOC_ID,
      outcome: 'complete',
      docs_reached: 1,
    })
    await flushPromises()
    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(true)

    store.dismiss(GROUP_ID)
    await flushPromises()

    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(false)
    expect(wrapper.findComponent(ConversationView).exists()).toBe(true)
  })

  // NR0003 required regression 5: the group store holds one run per group, but the condition is
  // per tab — activeAiInvokeGroupId follows the active tab while suppressDocRef comes from the
  // v-for's tab.id. A chat run left in the store must stop being suppressed the moment another
  // document becomes active, and be suppressed again on the way back.
  it('re-evaluates the condition per tab while one group run stays live', async () => {
    seedTabs([CH_TAB, TR_TAB], CH_TAB.id)
    const wrapper = mountPanel()
    await flushPromises()

    startRun(CH_TAB.id)
    await flushPromises()
    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(false)

    const tabs = useTabsStore()
    tabs.activeTabId = TR_TAB.id
    await flushPromises()
    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(true)

    tabs.activeTabId = CH_TAB.id
    await flushPromises()
    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(false)
  })
})
