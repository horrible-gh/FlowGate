import { shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useTabsStore, type Tab } from '@main/stores/tabs'

const { getRequest } = vi.hoisted(() => ({
  getRequest: vi.fn().mockResolvedValue({ data: { questions: [] } }),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('../composables/useShortcuts', () => ({
  useShortcuts: () => ({ register: vi.fn(), unregister: vi.fn() }),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

vi.mock('@main/composables/useFlowGateToken', () => ({
  useFlowGateToken: () => ({
    issueToken: vi.fn(),
    requestReview: vi.fn(),
    requestWorkflowDecision: vi.fn(),
    composeMention: vi.fn(() => ''),
  }),
  splitGroupId: () => ({ module: '', group: '' }),
}))

const MdViewerStub = defineComponent({
  name: 'MdViewer',
  props: {
    path: { type: String, default: '' },
    docId: { type: String, default: null },
    projectId: { type: String, default: null },
    gitGroupId: { type: String, default: null },
    gitCommit: { type: String, default: null },
  },
  setup(props) {
    return () => h('div', { class: 'md-viewer-stub', 'data-doc-id': props.docId ?? '' })
  },
})

const STUBS = {
  TabBar: true,
  DocHeader: true,
  DocWorkflow: true,
  MdViewer: MdViewerStub,
  TextViewer: true,
  DocInfoPanel: true,
  ReviewActionBar: true,
  ReviewRejectDialog: true,
  DesignHandoffDialog: true,
  NextActionModal: true,
  NextEmptyDocModal: true,
  CommandSelectorModal: true,
  QTDetailViewer: true,
  NewQModal: true,
  AiInvokeInline: true,
  GitFinalizePanel: true,
  ConfirmModal: true,
  TimeMachineDialog: true,
  MentionAddModal: true,
  ClipboardFallbackModal: true,
}

function mountWith(tab: Tab) {
  const store = useTabsStore()
  store.tabs = [tab]
  store.activeTabId = tab.id
  return shallowMount(MainPanel, {
    attachTo: document.body,
    global: { plugins: [i18n], stubs: { teleport: false, ...STUBS } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete window.__accessToken__
  getRequest.mockClear()
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('MainPanel MdViewer doc-id routing (0310)', () => {
  it('passes doc-id for a DB document tab even when typeCode is missing', () => {
    const docTab: Tab = {
      id: 'flowgate.default.0310.0008-TR',
      title: 'TR without metadata',
      path: '',
      type: 'md',
      typeCode: undefined,
      projectId: null,
    }

    const wrapper = mountWith(docTab)
    const viewer = wrapper.findComponent(MdViewerStub)

    expect(viewer.exists()).toBe(true)
    expect(viewer.props('docId')).toBe(docTab.id)
    expect(viewer.props('path')).toBe('')
    expect(viewer.props('projectId')).toBeNull()
  })

  it('keeps file tabs path-backed by suppressing doc-id', () => {
    const fileTab: Tab = {
      id: 'flowgate:/client/src/main/components/MainPanel.vue',
      title: 'MainPanel.vue',
      path: 'client/src/main/components/MainPanel.vue',
      type: 'md',
      typeCode: undefined,
      projectId: 'flowgate',
    }

    const wrapper = mountWith(fileTab)
    const viewer = wrapper.findComponent(MdViewerStub)

    expect(viewer.exists()).toBe(true)
    expect(viewer.props('docId')).toBeNull()
    expect(viewer.props('path')).toBe(fileTab.path)
    expect(viewer.props('projectId')).toBe('flowgate')
  })

  it('uses the same not-file-tab doc-id rule in full view', async () => {
    const docTab: Tab = {
      id: 'flowgate.default.0310.0009-T',
      title: 'T without metadata',
      path: '',
      type: 'md',
      typeCode: undefined,
      projectId: null,
    }

    const wrapper = mountWith(docTab)

    await wrapper.find('.card-actions .btn-secondary').trigger('click')
    await wrapper.vm.$nextTick()

    const viewers = wrapper.findAllComponents(MdViewerStub)
    expect(viewers).toHaveLength(2)
    expect(viewers[0].props('docId')).toBe(docTab.id)
    expect(viewers[1].props('docId')).toBe(docTab.id)
  })
})
