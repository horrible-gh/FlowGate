import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useTabsStore } from '@main/stores/tabs'
import { mountMainPanel } from '../helpers/mountMainPanel'

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

    const wrapper = await mountMainPanel({
      tabs: [
        {
          id: 'test.test2.0001.0004-DS',
          title: 'DS doc',
          path: '/ds.md',
          type: 'md',
          typeCode: 'DS',
        },
      ],
      stubs: {
        DocHeader: {
          template: '<div />',
          setup() {
            return {
              docLoaded: true,
              docProjectId: 'test-project',
              groupId: 'test-group',
              // 0394 T0016 항목 3 (거짓 초록 전수 확인): 여기에는 docReviewStatus:
              // 'pending_review' 와 nextStepExists: false 도 심어져 있었는데, 반대 값으로
              // 바꿔도 이 케이스의 판정은 바뀌지 않았다. 이 케이스는 onOpenHeadDocClick 을
              // 직접 호출해 openTab 에 넘어간 인자만 보기 때문이다 — 쓰지 않는 사전조건은
              // 읽는 사람에게 "리뷰 상태에 따라 달라진다"고 잘못 알려 주므로 지웠다.
              headDocId: headDocPayload.docId,
              headDocTitle: headDocPayload.title,
            }
          },
        },
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
    const wrapper = await mountMainPanel({
      deep: true,
      tabs: [
        {
          id: 'test.test2.0001.0005-D',
          title: 'D doc',
          path: '',
          type: 'md',
          typeCode: 'D',
        },
      ],
      // The real DocHeader is what this case is about.
      stubs: { DocHeader: false },
    })

    expect(wrapper.findComponent({ name: 'DocHeader' }).exists()).toBe(true)
  })

  it('passes canNextAction prop to ReviewActionBar (driven by workflowViewState)', async () => {
    // When resolveWorkflowViewState is mocked to return canNextAction:false,
    // getWorkflowViewState returns canNextAction=false and the bar is hidden
    // (mode=null would hide it, but our mock returns mode='review').
    // This test confirms the integration path is connected.
    const wrapper = await mountMainPanel({
      tabs: [
        {
          id: 'test.p.0001.0003-D',
          title: 'D doc',
          path: '',
          type: 'md',
          typeCode: 'D',
        },
      ],
    })

    const actionBar = wrapper.findComponent({ name: 'ReviewActionBar' })
    expect(actionBar.exists()).toBe(true)
    expect(actionBar.props('canNextAction')).toBe(false)
  })

  it('NR157 transport fix: headDocReviewStatus propagates through getWorkflowViewInput', async () => {
    // Regression test: previously headDocReviewStatus was hardcoded to null in getWorkflowViewInput,
    // causing rejected head docs to not trigger mode='rejected' in resolveWorkflowViewState.
    const wrapper = await mountMainPanel({
      tabs: [
        {
          id: 'test.test2.0002.0001-R',
          title: 'R doc',
          path: '',
          type: 'md',
          typeCode: 'R',
        },
      ],
      stubs: {
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
      },
    })

    const vm = wrapper.vm as any
    const input = vm.getWorkflowViewInput('test.test2.0002.0001-R')
    expect(input.headDocReviewStatus).toBe('rejected')
  })
})
