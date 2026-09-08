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
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const GROUP_ID = 'flowgate.default.0481'
const R_TAB = {
  id: `${GROUP_ID}.0001-R`,
  title: 'requirement',
  path: 'documents/flowgate/main/default/0481/0001-R_document.md',
  type: 'md',
  typeCode: 'R',
  projectId: 'flowgate',
}

const DocHeaderStub = defineComponent({
  name: 'DocHeader',
  setup(_props, { expose }) {
    expose({ groupId: ref(GROUP_ID) })
    return () => h('div', { class: 'doc-header-stub' })
  },
})

function mountPanel() {
  localStorage.setItem(
    'flowgate.user.guest.tabs',
    JSON.stringify({ tabs: [R_TAB], activeTabId: R_TAB.id }),
  )
  return mount(MainPanel, {
    attachTo: document.body,
    shallow: true,
    global: {
      plugins: [i18n],
      stubs: { teleport: false, DocHeader: DocHeaderStub, AiInvokeInline: false },
    },
  })
}

function startRun(actionScope?: string) {
  useAiInvokeRunsStore().trackStarted({
    run_id: 'run-0481',
    group_id: GROUP_ID,
    doc_ref: '',
    status: 'running',
    ...(actionScope ? { action_scope: actionScope } : {}),
  })
}

const finalizePanel = (wrapper: ReturnType<typeof mountPanel>) =>
  wrapper.findComponent({ name: 'GitFinalizePanel' })

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

/**
 * 0481 T0010, rejected twice on the same sentence: "채팅을 치면 기다렸다가 바로
 * 답장 받는걸 원했는데 아예 다이얼로그 밖으로 빠져나가서 기본 AI실행 다이얼로그를
 * 본다" (07:40) and "내가 요구한 채팅하면 다이얼로그 안빠져 나가는것도 안되어있음"
 * (10:33).
 *
 * Nothing navigated and nothing opened on top. GitFinalizePanel — which hosts the
 * conflict resolver AND the merge approval dialog, and therefore the chat inside
 * it — is mounted under `v-if="!aiRunDocumentLocked"`. The chat's own run is a
 * group run, so sending a message locked the column and DESTROYED the dialog that
 * sent it (its open flag is panel-local, so it never came back). rev1's in-place
 * wait was correct and never got to run.
 */
describe('MainPanel git-owned run cover branch (0481)', () => {
  it('keeps the git panel (and its dialogs) mounted while its own conflict run works', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    expect(finalizePanel(wrapper).exists()).toBe(true)

    startRun('resolve_conflict')
    await flushPromises()

    expect(finalizePanel(wrapper).exists()).toBe(true)
    expect(wrapper.find('.ai-invoke-status-card').exists()).toBe(false)
  })

  it('still covers the column for an ordinary group run', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    startRun('edit')
    await flushPromises()

    expect(finalizePanel(wrapper).exists()).toBe(false)
    expect(wrapper.find('.ai-invoke-status-card').exists()).toBe(true)
  })

  it('still covers the column when the server does not name a scope', async () => {
    // The escape hatch is opt-in: an unlabelled run is an ordinary run.
    const wrapper = mountPanel()
    await flushPromises()

    startRun()
    await flushPromises()

    expect(finalizePanel(wrapper).exists()).toBe(false)
  })

  it('does not lose the exception on the poll that follows the SSE frame', async () => {
    // `refresh()` rebuilds the entry from GET /ai-invoke/{run_id} every 5s. While
    // that response omitted action_scope, the panel survived the start event and
    // was torn down moments later — the same symptom, just delayed.
    const wrapper = mountPanel()
    await flushPromises()
    startRun('resolve_conflict')
    await flushPromises()

    const store = useAiInvokeRunsStore()
    getRequest.mockResolvedValueOnce({
      data: {
        ok: true,
        run_id: 'run-0481',
        status: 'running',
        group_id: GROUP_ID,
        action_scope: 'resolve_conflict',
        mode: 'single',
        docs_target: 1,
      },
    })
    await store.refresh(GROUP_ID)
    await flushPromises()

    expect(store.runsByGroup[GROUP_ID].actionScope).toBe('resolve_conflict')
    expect(finalizePanel(wrapper).exists()).toBe(true)
  })
})
