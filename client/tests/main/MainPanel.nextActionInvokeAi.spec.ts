import { mount, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import NextActionModal from '@main/components/NextActionModal.vue'

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
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
    composeMention: (token: any) => token?.mention ?? '',
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

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
      },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
})

// 0366 T0004 (rev3 rejection): "[다음 단계 진행] > [AI 호출]을 열면 다이얼로그가 이전
// 버전(삭제 전 버전)으로 나온다". The proceed dialog's [AI 호출] was still wired to the old
// standalone AiInvokeDialog('new') — the exact entry that was deleted from the action bar.
// It must open the same continuous-work dialog the action-bar [AI 호출] opens.
describe('MainPanel proceed-dialog [AI 호출]', () => {
  it('opens the continuous-work dialog, not the old standalone invoke dialog', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any

    vm.nextActionModalTabId = 'test.none.0366.0004-T'
    vm.nextActionModalDocRef = 'test.none.0366.0001-R'
    vm.nextActionModalProjectId = 'test'
    vm.nextActionModalGroupId = 'test.none.0366'

    // Drive it exactly as the UI does: the proceed dialog closes itself and emits invoke-ai.
    const modal = wrapper.findComponent({ name: 'NextActionModal' })
    expect(modal.exists()).toBe(true)
    modal.vm.$emit('invoke-ai', [])
    await wrapper.vm.$nextTick()

    expect(vm.continuousDialogVisible).toBe(true)
    expect(vm.aiInvokeVisible).toBe(false)
    expect(vm.continuousTabId).toBe('test.none.0366.0004-T')
    expect(vm.continuousDocRef).toBe('test.none.0366.0001-R')
    expect(vm.continuousProjectId).toBe('test')
    expect(vm.continuousGroupId).toBe('test.none.0366')

    wrapper.unmount()
  })

  // The other half of the chain the reviewer walked: [다음 단계 진행] opens NextActionModal,
  // whose proceed dropdown [AI 호출] is what emits invoke-ai.
  it('is reached from the real proceed dialog [AI 호출] item', async () => {
    const modal = mount(NextActionModal, {
      attachTo: document.body,
      props: {
        visible: true,
        nextStepLabel: 'T',
        projectId: 'test',
        groupId: 'test.none.0366',
        docRef: 'test.none.0366.0001-R',
      },
      global: { plugins: [i18n], stubs: { AppIcon: true } },
    })
    await modal.vm.$nextTick()

    const items = Array.from(document.body.querySelectorAll('.nad-proceed-item')) as HTMLElement[]
    const aiItem = items.find(el => (el.textContent ?? '').includes('AI'))
    expect(aiItem).toBeTruthy()
    aiItem!.click()
    await modal.vm.$nextTick()

    expect(modal.emitted('invoke-ai')).toHaveLength(1)
    modal.unmount()
    document.body.innerHTML = ''
  })

  it('reports missing workflow info instead of opening either dialog', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any

    vm.nextActionModalTabId = 'test.none.0366.0004-T'
    vm.nextActionModalDocRef = ''
    vm.nextActionModalProjectId = ''
    vm.nextActionModalGroupId = ''

    vm.onNextActionInvokeAi([])
    await wrapper.vm.$nextTick()

    expect(vm.continuousDialogVisible).toBe(false)
    expect(vm.aiInvokeVisible).toBe(false)

    wrapper.unmount()
  })
})
