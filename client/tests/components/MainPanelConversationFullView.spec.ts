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
function mountPanel(stubs: Record<string, unknown> = {}) {
  return mount(MainPanel, {
    attachTo: document.body,
    shallow: true,
    global: {
      plugins: [i18n],
      stubs: { teleport: false, DocHeader: DocHeaderStub, AiInvokeInline: false, ...stubs },
    },
  })
}

// The real ConversationView, so the re-pin below travels the actual seam (MainPanel's ref ->
// the component's exposed scrollToBottom -> a write to .conv-scroll) instead of a stub's
// say-so. Its own children stay stubbed.
function mountPanelWithRealChat() {
  return mountPanel({ ConversationView: false })
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

describe('MainPanel CH full view', () => {
  it('renders exactly one ConversationView inside the chat card', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.findAllComponents(ConversationView)).toHaveLength(1)
    const conversation = document.body.querySelector('conversation-view-stub')
    expect(conversation).not.toBeNull()
    expect(conversation!.parentElement!.className).toContain('conv-card-bd')
  })

  // 0263 R0001: the chat had a [Full View] button (0246), which 0251 rev1 removed while chasing
  // an unrelated overlay bug. The chat is the surface that needs the height most — the original
  // ask was a tablet screen too short to hold a conversation.
  it('offers a full view action on the chat card', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const action = wrapper.find('.conv-card .card-actions button')
    expect(action.exists()).toBe(true)
    expect(action.text()).toContain('Full View')
  })

  // The heart of the fix, and why this is a Teleport rather than a second ConversationView in
  // the dialog: a chat holds state no re-mount can reproduce — the poll loop of an in-flight AI
  // call, its spinner, the unsent draft. Asserting the very same DOM node lands in the dialog is
  // what pins "moved, not re-created"; a fresh mount would satisfy any weaker check.
  it('moves the live chat instance into the dialog instead of mounting a second one', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const chatEl = wrapper.findComponent(ConversationView).element

    await wrapper.find('.conv-card .card-actions button').trigger('click')
    await flushPromises()

    expect(document.body.querySelector('.document-modal')).not.toBeNull()
    const mounted = document.body.querySelectorAll('conversation-view-stub')
    expect(mounted).toHaveLength(1)
    expect(mounted[0]).toBe(chatEl)
    expect(mounted[0].parentElement!.className).toContain('document-modal__body--conversation')
    expect(wrapper.findAllComponents(ConversationView)).toHaveLength(1)
  })

  it('returns that same chat instance to its card when the full view closes', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const chatEl = wrapper.findComponent(ConversationView).element

    await wrapper.find('.conv-card .card-actions button').trigger('click')
    await flushPromises()
    expect(chatEl.parentElement!.className).toContain('document-modal__body--conversation')

    ;(document.body.querySelector('.document-modal .modal-close') as HTMLElement).click()
    await flushPromises()

    expect(document.body.querySelector('.document-modal')).toBeNull()
    const mounted = document.body.querySelectorAll('conversation-view-stub')
    expect(mounted).toHaveLength(1)
    expect(mounted[0]).toBe(chatEl)
    expect(mounted[0].parentElement!.className).toContain('conv-card-bd')
  })

  // Moving a node re-attaches it, and a re-attached scroll container comes back at the top —
  // so the full view would open on the OLDEST message, the "it updates to the top" symptom
  // TR0044.0010 rev8 already fixed once for new turns. jsdom has no layout engine and never
  // resets scrollTop, so the reset itself cannot be reproduced here; what is asserted is the
  // repair that answers it — after the move the chat really is pinned to the bottom, through
  // the real component's exposed scrollToBottom. Only the geometry (scrollHeight) is supplied,
  // since jsdom reports 0 for every element.
  it('pins the chat to the newest message after the move', async () => {
    const wrapper = mountPanelWithRealChat()
    await flushPromises()

    const scroll = document.body.querySelector('.conv-scroll') as HTMLElement
    expect(scroll).not.toBeNull()
    Object.defineProperty(scroll, 'scrollHeight', { value: 900, configurable: true })
    scroll.scrollTop = 0

    await wrapper.find('.conv-card .card-actions button').trigger('click')
    await flushPromises()

    expect(scroll.parentElement!.closest('.document-modal__body--conversation')).not.toBeNull()
    expect(scroll.scrollTop).toBe(900)
  })

  // The chat is not an editable transcript — it is written through its composer — so the
  // dialog's [Edit] stays off for CH, exactly as the card offers no [Edit].
  it('offers no edit action in the chat full view', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('.conv-card .card-actions button').trigger('click')
    await flushPromises()

    const buttons = [...document.body.querySelectorAll('.document-modal .modal-hd-actions button')]
    expect(buttons.some((b) => b.textContent!.includes('Edit'))).toBe(false)
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
