// MainPanel — 다음 액션이 작업계획(WP)일 때의 갈림길 (flowgate.default.0405 T0007).
//
// P0004 [제안 창 열기] / [작업계획이 아닌 타입 — 기존 공용 경로가 그대로다]:
// 다음 타입이 WP 면 공용 진행 목록 대신 전용 제안 창이 열리고, 그 밖의 타입은 지금까지와
// 똑같이 진행 목록이 열린다. 그리고 [멘트복사]·[AI 호출]이 같은 범위를 실어 보낸다.
import { shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useTabsStore } from '@main/stores/tabs'

const postRequest = vi.fn()
const advanceWithWorkPlanScope = vi.fn()

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
  patchRequest: vi.fn(),
  postRequest: (...args: any[]) => postRequest(...args),
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
    advanceWithWorkPlanScope: (...args: any[]) => advanceWithWorkPlanScope(...args),
    requestReview: vi.fn(),
    requestWorkflowDecision: vi.fn(),
    composeMention: (token: any) => token?.mention ?? '',
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: (gid: string) => {
    const parts = String(gid).split('.')
    return parts.length === 3 ? { module: parts[1], groupCode: parts[2] } : { groupCode: gid }
  },
}))

vi.mock('@main/utils/clipboard', async (importOriginal) => {
  const actual = await importOriginal<any>()
  return {
    ...actual,
    copyToClipboardDeferred: async (produce: () => Promise<string>) => {
      try {
        await produce()
        return true
      } catch {
        return false
      }
    },
    copyToClipboard: vi.fn().mockResolvedValue(true),
  }
})

const TAB_ID = 'flowgate.default.0405.0001-R'
const GROUP_ID = 'flowgate.default.0405'
const SCOPE = {
  quantity_type_codes: ['DS'],
  provider_ids: ['aip_opus', 'aip_sonnet'],
}

function mountPanel() {
  const tabs = useTabsStore()
  tabs.tabs = [{ id: TAB_ID, title: 'R', path: '', type: 'md', typeCode: 'R' } as any]
  tabs.activeTabId = TAB_ID
  return shallowMount(MainPanel, {
    global: {
      plugins: [i18n],
      stubs: {
        TabBar: true, DocHeader: true, DocWorkflow: true, MdViewer: true, TextViewer: true,
        DocInfoPanel: true, ReviewActionBar: true, ReviewRejectDialog: true,
        DesignHandoffDialog: true, NextActionModal: true, NextEmptyDocModal: true,
        WorkPlanCreateDialog: true, WorkPlanProposalDialog: true, CommandSelectorModal: true,
        QTDetailViewer: true, NewQModal: true,
      },
    },
  })
}

function seedHead(vm: any, headType: string) {
  vm.docHeaderRefs[TAB_ID] = {
    docReviewStatus: 'wf_in_progress',
    workflowSteps: [headType],
    workflowRootType: 'R',
    workflowHeadType: headType,
    workflowHeadIndex: 0,
    headStatus: 'pending',
    headDocId: null,
    groupId: GROUP_ID,
    docProjectId: 'flowgate',
    docModule: 'default',
  }
}

function seedProposalContext(vm: any) {
  vm.workPlanCreateParentDocId = TAB_ID
  vm.workPlanCreateProjectId = 'flowgate'
  vm.workPlanCreateGroupId = GROUP_ID
  vm.workPlanProposalVisible = true
  vm.workPlanProposalNotice = ''
  vm.workPlanProposalBusy = ''
}

describe('MainPanel — 다음 액션이 작업계획일 때', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
    postRequest.mockReset()
    advanceWithWorkPlanScope.mockReset()
  })

  it('WP 면 전용 제안 창이 열리고 공용 진행 목록은 열리지 않는다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedHead(vm, 'WP')
    vm.onNextActionClick(TAB_ID)
    await wrapper.vm.$nextTick()

    expect(vm.workPlanProposalVisible).toBe(true)
    expect(vm.nextActionModalVisible).toBe(false)
    expect(vm.workPlanCreateParentDocId).toBe(TAB_ID)
    expect(vm.workPlanCreateGroupId).toBe(GROUP_ID)
  })

  it('WP 가 아니면 지금까지와 같이 진행 목록이 열린다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedHead(vm, 'D')
    vm.onNextActionClick(TAB_ID)
    await wrapper.vm.$nextTick()

    expect(vm.nextActionModalVisible).toBe(true)
    expect(vm.workPlanProposalVisible).toBe(false)
  })

  it('[멘트복사]는 범위를 실어 advance 를 부르고 성공하면 창을 닫는다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedProposalContext(vm)
    advanceWithWorkPlanScope.mockResolvedValue({
      token: { raw_token: 'tok', token_id: 't1', mention: '## 작업계획 맡길 범위', doc_ref: TAB_ID },
      error: null,
    })

    await vm.onWorkPlanProposalCopyMention(SCOPE)

    expect(advanceWithWorkPlanScope).toHaveBeenCalledWith({
      docId: TAB_ID, workPlanScope: SCOPE, refDocIds: [TAB_ID],
    })
    expect(vm.workPlanProposalVisible).toBe(false)
    expect(vm.workPlanProposalBusy).toBe('')
  })

  it('409 면 복사하지 않고 사유 한 줄만 남긴 채 창을 열어 둔다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedProposalContext(vm)
    advanceWithWorkPlanScope.mockResolvedValue({
      token: null, error: { code: 'sequence_exhausted' },
    })

    await vm.onWorkPlanProposalCopyMention(SCOPE)

    expect(vm.workPlanProposalVisible).toBe(true)
    expect(vm.workPlanProposalNotice).toBe(
      i18n.global.t('main.work_plan_proposal_dialog.notice_sequence_exhausted'),
    )
  })

  it('[AI 호출]은 work_plan_proposal 을 single 로 시작한다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedProposalContext(vm)
    postRequest.mockResolvedValue({ data: { ok: true, run_id: 'aiv_1' } })

    await vm.onWorkPlanProposalInvokeAi({ scope: SCOPE, providerId: 'aip_opus' })

    const [url, body] = postRequest.mock.calls[0]
    expect(url).toBe('/api/v1/ai-invoke/start')
    expect(body).toMatchObject({
      project: 'flowgate',
      module: 'default',
      group: '0405',
      doc_ref: TAB_ID,
      action_scope: 'work_plan_proposal',
      mode: 'single',
      provider_id: 'aip_opus',
      selected_docs: [TAB_ID],
      work_plan_scope: SCOPE,
    })
    expect(vm.workPlanProposalVisible).toBe(false)
  })

  it('호출 경합으로 다른 실행을 확인하면 제안 창을 즉시 닫는다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedProposalContext(vm)
    postRequest.mockRejectedValue({
      response: { data: { code: 'run_in_progress', message: 'An AI run is already in progress.' } },
    })

    await vm.onWorkPlanProposalInvokeAi({ scope: SCOPE, providerId: 'aip_opus' })

    // 반응형 store가 SSE를 받기 전의 경합도 fail-closed로 처리한다.
    expect(vm.workPlanProposalVisible).toBe(false)
    expect(vm.workPlanProposalNotice).toBe('')
  })

  // T0009 — 실제 화면에서 [다음 단계 진행]은 워크플로 시퀀스의 머리 칸이고, 그 칸이 작업계획일
  // 때 DocWorkflow 는 next-action 이 아니라 create-work-plan 을 올린다(0395 T0036 이 고정한
  // 계약). 그래서 전용 제안 창은 이 이벤트의 처리에도 걸려 있어야 한다.
  it('시퀀스의 작업계획 칸을 눌렀을 때도 전용 제안 창이 열린다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedHead(vm, 'WP')
    vm.onCreateWorkPlan(TAB_ID, TAB_ID)
    await wrapper.vm.$nextTick()

    expect(vm.workPlanProposalVisible).toBe(true)
    expect(vm.workPlanCreateVisible).toBe(false)
    expect(vm.workPlanCreateParentDocId).toBe(TAB_ID)
    expect(vm.workPlanCreateGroupId).toBe(GROUP_ID)
  })

  it('머리 칸이 작업계획이 아닌 채로 올라오면 기존 생성 대화상자로 간다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedHead(vm, 'D')
    vm.onCreateWorkPlan(TAB_ID, TAB_ID)
    await wrapper.vm.$nextTick()

    expect(vm.workPlanProposalVisible).toBe(false)
    expect(vm.workPlanCreateVisible).toBe(true)
  })
})
