import { mount, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import NextActionModal from '@main/components/NextActionModal.vue'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'

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
        WorkPlanCreateDialog: true,
        CommandSelectorModal: true,
        QTDetailViewer: true,
        NewQModal: true,
      },
    },
  })
}

function seedNextAction(vm: any, typeCode: string) {
  vm.nextActionModalTabId = 'test.test.0002.0002-TS'
  vm.nextActionModalDocRef = 'test.test.0002.0001-R'
  vm.nextActionModalProjectId = 'test'
  vm.nextActionModalGroupId = 'test.test.0002'
  vm.nextActionModalModuleName = 'test'
  vm.nextActionModalTypeCode = typeCode
}

// 0395 T0026 재작업 — 사용자가 신고한 길: 워크플로 머리 칸이 작업계획인 그룹에서
// [빈 문서 만들기]로 문서를 만들었더니, 열 때마다
//   "이 작업계획을 표로 열 수 없습니다 / Expecting value: line 1 column 1 (char 0)"
// 만 나왔다. 제목만 받는 빈 문서 대화상자가 수량도 공급자도 없는 파일을 만들었기 때문이다.
// 작업계획에는 "빈 문서"가 없다 — 같은 자리에서 계획을 끝까지 정하는 대화상자를 연다.
describe('MainPanel [빈 문서 만들기] — 머리 칸이 작업계획일 때', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
  })

  it('작업계획 생성 대화상자를 열고, 빈 문서 대화상자는 열지 않는다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedNextAction(vm, 'WP')

    const modal = wrapper.findComponent({ name: 'NextActionModal' })
    expect(modal.exists()).toBe(true)
    modal.vm.$emit('create-empty')
    await wrapper.vm.$nextTick()

    expect(vm.workPlanCreateVisible).toBe(true)
    expect(vm.nextEmptyDocModalVisible).toBe(false)
    // 계획은 시퀀스를 가진 뿌리 문서에 붙는다 — 보고 있던 탭이 아니다.
    expect(vm.workPlanCreateParentDocId).toBe('test.test.0002.0001-R')
    expect(vm.workPlanCreateProjectId).toBe('test')
    expect(vm.workPlanCreateGroupId).toBe('test.test.0002')

    wrapper.unmount()
  })

  it('다른 타입은 지금까지와 똑같이 빈 문서 대화상자를 연다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedNextAction(vm, 'N')

    wrapper.findComponent({ name: 'NextActionModal' }).vm.$emit('create-empty')
    await wrapper.vm.$nextTick()

    expect(vm.nextEmptyDocModalVisible).toBe(true)
    expect(vm.nextEmptyDocType).toBe('N')
    expect(vm.workPlanCreateVisible).toBe(false)

    wrapper.unmount()
  })
})

const entryLabelCases = [
  ['ko', '작업계획 생성'],
  ['en', 'Create Work Plan'],
  ['ja', '作業計画作成'],
] as const

function mountActionBar(nextStepCode: string) {
  return mount(ReviewActionBar, {
    props: {
      docId: 'test.test.0002.0002-TS',
      projectId: 'test',
      groupId: 'test.test.0002',
      docRef: 'test.test.0002.0001-R',
      reviewStatus: 'approved',
      mode: 'next',
      nextStepLabel: nextStepCode,
      nextStepCode,
    },
    global: { plugins: [i18n], stubs: { teleport: true, AppIcon: true } },
  })
}

function mountNextAction(nextTypeCode: string) {
  return mount(NextActionModal, {
    props: {
      visible: true,
      nextTypeCode,
      nextStepLabel: nextTypeCode,
      projectId: 'test',
      groupId: 'test.test.0002',
      docRef: 'test.test.0002.0001-R',
    },
    global: { plugins: [i18n], stubs: { teleport: true, AppIcon: true } },
  })
}

describe('작업계획 다음 단계 생성 라벨', () => {
  it.each(entryLabelCases)('액션바가 %s 로케일의 [작업계획 생성]을 렌더하고 기존 create-empty 이벤트를 유지한다', async (locale, label) => {
    i18n.global.locale.value = locale
    const wrapper = mountActionBar('WP')
    await wrapper.get('.ab-dd-toggle').trigger('click')
    const item = wrapper.findAll('.ab-split-item').find(node => node.text().includes(label))
    expect(item).toBeDefined()
    await item!.trigger('click')
    expect(wrapper.emitted('create-empty')).toHaveLength(1)
    wrapper.unmount()
  })

  it.each(entryLabelCases)('[다음 단계] 대화상자가 %s 로케일의 [작업계획 생성]을 렌더하고 기존 create-empty 이벤트를 유지한다', async (locale, label) => {
    i18n.global.locale.value = locale
    const wrapper = mountNextAction('WP')
    const item = wrapper.get('.nad-proceed-item')
    expect(item.text()).toContain(label)
    await item.trigger('click')
    expect(wrapper.emitted('create-empty')).toHaveLength(1)
    wrapper.unmount()
  })

  it('작업계획이 아닌 다음 단계는 두 화면 모두 [빈 문서 생성]을 유지한다', async () => {
    i18n.global.locale.value = 'ko'
    const actionBar = mountActionBar('T')
    await actionBar.get('.ab-dd-toggle').trigger('click')
    expect(actionBar.findAll('.ab-split-item').some(node => node.text().includes('빈 문서 생성'))).toBe(true)

    const modal = mountNextAction('T')
    expect(modal.get('.nad-proceed-item').text()).toContain('빈 문서 생성')

    actionBar.unmount()
    modal.unmount()
  })
})
