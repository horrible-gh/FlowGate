// flowgate.default.0467 T0012 item 4 — MainPanel.saveEditContent() must call the active
// TR tab's [단계별 확인] card fetchData() through a bindActiveRef() registry, the same
// pattern mdViewerRefs/textViewerRefs already use (see MainPanel.docHeaderRefIdentity.spec.ts
// for the docHeaderRefs precedent this test harness mirrors), and only on a *successful*
// save — a failed save must leave the card on its prior state (D0005 §3 last paragraph).
import { flushPromises, shallowMount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useTabsStore } from '@main/stores/tabs'

const { getRequest, patchRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest,
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
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

const fetchDataSpy = vi.fn()

function mountPanel() {
  return shallowMount(MainPanel, {
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
        StepVerificationCard: {
          props: ['docId'],
          template: '<div class="step-verify-card-stub" />',
          setup(_props: any, { expose }: any) {
            expose({ fetchData: fetchDataSpy })
          },
        },
      },
    },
  })
}

const trTab = {
  id: 'flowgate.default.0467.9101-TR',
  title: 'tr',
  path: 'documents/flowgate/main/default/0467/9101-TR_document.md',
  type: 'md' as const,
  typeCode: 'TR',
  projectId: 'flowgate',
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  patchRequest.mockReset()
  fetchDataSpy.mockReset()
  getRequest.mockResolvedValue({ data: { content: '# TR\n\n## 단계별 확인\n\n없음\n' } })
})

describe('MainPanel → [단계별 확인] 카드 저장 후 재조회 배선 (T0012 item 4)', () => {
  it('본문 저장이 성공하면 활성 탭의 카드 fetchData()를 호출한다', async () => {
    patchRequest.mockResolvedValue({ data: {} })
    const wrapper = mountPanel()
    useTabsStore().openTab({ ...trTab })
    await nextTick()
    await flushPromises()

    const vm = wrapper.vm as any
    await vm.openEditModal({ ...trTab })
    await flushPromises()
    fetchDataSpy.mockClear()

    await vm.saveEditContent()
    await flushPromises()

    expect(patchRequest).toHaveBeenCalledWith(
      '/api/v1/documents/content',
      expect.objectContaining({ doc_id: trTab.id }),
    )
    expect(fetchDataSpy).toHaveBeenCalledTimes(1)
  })

  it('본문 저장이 실패하면 카드를 다시 조회하지 않고 직전 상태를 유지한다', async () => {
    patchRequest.mockRejectedValue(new Error('save failed'))
    const wrapper = mountPanel()
    useTabsStore().openTab({ ...trTab })
    await nextTick()
    await flushPromises()

    const vm = wrapper.vm as any
    await vm.openEditModal({ ...trTab })
    await flushPromises()
    fetchDataSpy.mockClear()

    await vm.saveEditContent()
    await flushPromises()

    expect(fetchDataSpy).not.toHaveBeenCalled()
  })
})
