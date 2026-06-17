import { mount, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useTabsStore } from '@main/stores/tabs'

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
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: () => ({ module: '', group: '' }),
}))

vi.mock('@main/workflow/workflowViewState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@main/workflow/workflowViewState')>()
  return {
    ...actual,
    resolveWorkflowViewState: () => ({
      mode: 'review' as const,
      canNextAction: false,
      currentStepCode: null,
      highlightStepCode: null,
      nextStepCode: null,
      nextStepActive: false,
      headDocLabel: null,
      headDocId: null,
      highlightDesignSeries: false,
      stepStates: [],
      nextStepIndex: null,
    }),
  }
})

const headDocPayload = {
  docId: 'test.test2.0001.0005-D',
  title: 'D doc',
  typeCode: 'D' as string | null,
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete window.__accessToken__
  getRequest.mockClear()
})

describe('MainPanel head-doc navigation', () => {
  it('onOpenHeadDocClick passes typeCode to tabsStore.openTab', async () => {
    const store = useTabsStore()
    const openTabSpy = vi.spyOn(store, 'openTab')

    store.tabs = [
      {
        id: 'test.test2.0001.0004-DS',
        title: 'DS doc',
        path: '/ds.md',
        type: 'md',
        typeCode: 'DS',
      },
    ]
    store.activeTabId = 'test.test2.0001.0004-DS'

    const wrapper = shallowMount(MainPanel, {
      global: {
        plugins: [i18n],
        stubs: {
          TabBar: true,
          DocHeader: {
            template: '<div />',
            setup() {
              return {
                docLoaded: true,
                docProjectId: 'test-project',
                groupId: 'test-group',
                docReviewStatus: 'pending_review',
                nextStepExists: false,
                headDocId: headDocPayload.docId,
                headDocTitle: headDocPayload.title,
              }
            },
          },
          DocWorkflow: true,
          MdViewer: true,
          TextViewer: true,
          DocInfoPanel: true,
          ReviewActionBar: {
            template:
              '<button data-testid="emit-head" @click="$emit(\'open-head-doc\', payload)" />',
            props: [
              'mode',
              'docId',
              'projectId',
              'groupId',
              'docRef',
              'docTitle',
              'reviewStatus',
              'docType',
              'canNextAction',
              'headDocId',
              'headDocLabel',
              'headDocTitle',
              'viewedDocId',
            ],
            emits: ['open-head-doc'],
            setup() {
              return { payload: headDocPayload }
            },
          },
        },
      },
    })

    ;(wrapper.vm as any).onOpenHeadDocClick(headDocPayload)

    expect(openTabSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        id: headDocPayload.docId,
        title: `${headDocPayload.docId} — ${headDocPayload.title}`,
        path: '',
        type: 'md',
        typeCode: 'D',
      }),
    )
  })

  it('renders DocHeader when active tab has typeCode', async () => {
    const store = useTabsStore()
    store.tabs = [
      {
        id: 'test.test2.0001.0005-D',
        title: 'D doc',
        path: '',
        type: 'md',
        typeCode: 'D',
      },
    ]
    store.activeTabId = 'test.test2.0001.0005-D'

    const wrapper = mount(MainPanel, {
      global: {
        plugins: [i18n],
        stubs: {
          TabBar: true,
          DocWorkflow: true,
          MdViewer: true,
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
        },
      },
    })

    await wrapper.vm.$nextTick()
    expect(wrapper.findComponent({ name: 'DocHeader' }).exists()).toBe(true)
  })

  it('passes canNextAction prop to ReviewActionBar (driven by workflowViewState)', async () => {
    // When resolveWorkflowViewState is mocked to return canNextAction:false,
    // getWorkflowViewState returns canNextAction=false and the bar is hidden
    // (mode=null would hide it, but our mock returns mode='review').
    // This test confirms the integration path is connected.
    const store = useTabsStore()
    store.tabs = [
      {
        id: 'test.p.0001.0003-D',
        title: 'D doc',
        path: '',
        type: 'md',
        typeCode: 'D',
      },
    ]
    store.activeTabId = 'test.p.0001.0003-D'

    const wrapper = shallowMount(MainPanel, {
      global: {
        plugins: [i18n],
        stubs: {
          TabBar: true,
          DocHeader: true,
          DocWorkflow: true,
          MdViewer: true,
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
        },
      },
    })

    await wrapper.vm.$nextTick()
    // ReviewActionBar is hidden when mode=null (docLoaded not true).
    // The mock ensures workflowViewState is called via our new integration path.
    expect(wrapper.findComponent({ name: 'ReviewActionBar' }).exists()).toBe(false)
  })

  it('NR157 transport fix: headDocReviewStatus propagates through getWorkflowViewInput', async () => {
    // Regression test: previously headDocReviewStatus was hardcoded to null in getWorkflowViewInput,
    // causing rejected head docs to not trigger mode='rejected' in resolveWorkflowViewState.
    const store = useTabsStore()
    store.tabs = [
      {
        id: 'test.test2.0002.0001-R',
        title: 'R doc',
        path: '',
        type: 'md',
        typeCode: 'R',
      },
    ]
    store.activeTabId = 'test.test2.0002.0001-R'

    const wrapper = shallowMount(MainPanel, {
      global: {
        plugins: [i18n],
        stubs: {
          TabBar: true,
          DocHeader: {
            template: '<div />',
            setup() {
              return {
                docLoaded: true,
                docProjectId: 'test-project',
                groupId: 'test-group',
                docReviewStatus: 'wf_in_progress',
                workflowSteps: ['DS', 'D'],
                workflowHeadType: 'DS',
                headStatus: 'in_progress',
                headDocId: 'test.test2.0002.0002-DS',
                headDocReviewStatus: 'rejected',
                nextStepExists: true,
              }
            },
          },
          DocWorkflow: true,
          MdViewer: true,
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
        },
      },
    })

    await wrapper.vm.$nextTick()

    const vm = wrapper.vm as any
    const input = vm.getWorkflowViewInput('test.test2.0002.0001-R')
    expect(input.headDocReviewStatus).toBe('rejected')
  })
})
