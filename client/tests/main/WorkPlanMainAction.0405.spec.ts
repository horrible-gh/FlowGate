// 작업계획 생성이 이 화면의 메인 작업이다 — flowgate.default.0405 T0011 rev2.
//
// 사용자 반려(0011-T + 0012-TR 반려 2회):
//   "아직도 [AI호출]이 주인공이다. [AI호출]이 아니라 [작업계획 생성]이 주인공이여야 한다."
//   "AI 호출을 없애고 그자리에 넣어야할거아냐"  /  "문서생성이 아니라 AI호출을 강조해야지"
//   "맡길 단계??? 이건 대체 왜나와"
//   rev2 → "액션바에 [작업계획 생성] 추가해서 바로 작업계획 생성할수 있게 해야지"
//   rev2 → "AI공급자 선택할게 없으면 [2 후보공급자]는 안나오게 하고 1만 선택하고 생성할수 있게"
//   rev2 → "AI공급자 선택할게 없으면 [AI호출이 의미 없잖아] [+ 문서생성] 이 맨 우측으로"
//
// 그래서 이 파일이 못 박는 것.
//   ① 다음 단계가 작업계획이면 액션바 자체에 [작업계획 생성] 단추가 드러나 있고, 한 번
//      눌러 바로 열린다 (꺾쇠 목록을 열 필요가 없다). 그 목록에는 [AI 호출]이 없고 맨 아래
//      자리(예전 [AI 호출] 자리)를 [작업계획 생성]이 차지한다
//   ② [작업계획 생성]을 어디서 누르든 같은 창이 열린다 — [+ 생성] 하나뿐인 예전 생성
//      대화상자로 새는 길이 없다
//   ③ 공급자가 있는 창의 파란 주버튼은 [AI 호출] 하나뿐이고 '맡길 단계'는 어디에도 없다
//   ④ 공급자가 하나도 없으면 ② 칸도 [AI 호출]도 없고, [+ 문서생성]이 맨 오른쪽 주버튼이다
import { mount, shallowMount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import NextActionModal from '@main/components/NextActionModal.vue'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import WorkPlanProposalDialog from '@main/components/WorkPlanProposalDialog.vue'
import { useDocTypeStore } from '@main/stores/docTypeStore'
import { useAiProviderStore } from '@main/stores/aiProvider'

const getRequest = vi.fn()
const postRequest = vi.fn()

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: (...args: any[]) => getRequest(...args),
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
    advanceWithWorkPlanScope: vi.fn(),
    requestReview: vi.fn(),
    requestWorkflowDecision: vi.fn(),
    composeMention: (token: any) => token?.mention ?? '',
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

const TYPES = [
  { id: 21, code: 'DS', label: '설계지시', category: 'design', countable: true, unit: 'sheet' },
  { id: 32, code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR' },
]

const PROVIDERS = [
  { id: 'aip_opus', name: 'Claude Opus 5', kind: 'claude', exec_type: 'cli' },
  { id: 'aip_haiku', name: 'Claude Haiku', kind: 'claude', exec_type: 'cli' },
]

function mountActionBar(nextStepCode: string, canNextAction = true) {
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
      canNextAction,
    },
    global: { plugins: [i18n], stubs: { teleport: true, AppIcon: true } },
  })
}

function mountPanel() {
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

/** providers: [] 로 부르면 "등록된 AI 공급자가 하나도 없는 프로젝트"의 창이 열린다. */
async function mountDialog(providers = PROVIDERS) {
  const docTypeStore = useDocTypeStore()
  docTypeStore.items = TYPES as any
  docTypeStore.labelMap = Object.fromEntries(TYPES.map((item) => [item.code, item.label]))
  const providerStore = useAiProviderStore()
  providerStore.providers = providers as any
  providerStore.loadedProjectId = 'flowgate'
  getRequest.mockResolvedValue({ data: { providers, default_provider_id: providers[0]?.id ?? null } })
  const wrapper = mount(WorkPlanProposalDialog, {
    props: {
      visible: true,
      parentDocId: 'flowgate.default.0405.0001-R',
      projectId: 'flowgate',
      groupId: 'flowgate.default.0405',
    },
    global: { plugins: [i18n], stubs: { teleport: true, AppIcon: true } },
  })
  await flushPromises()
  return wrapper
}

describe('① 액션바 — [작업계획 생성]이 바 위에 그대로 드러나 있다', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
    getRequest.mockReset()
    postRequest.mockReset()
    getRequest.mockResolvedValue({ data: { providers: PROVIDERS, default_provider_id: 'aip_opus' } })
  })

  it('작업계획이 다음 단계면 액션바에서 바로 누를 수 있다', async () => {
    const wrapper = mountActionBar('WP')
    const main = wrapper.get('[data-test="ab-create-work-plan"]')
    expect(main.text()).toContain('작업계획 생성')
    expect(main.classes()).toContain('btn-primary')
    // 목록을 여는 단추가 아니다 — 한 번 누르면 곧바로 작업계획 창이 열린다.
    await main.trigger('click')
    expect(wrapper.emitted('create-empty')).toBeTruthy()
    expect(wrapper.findAll('.ab-split-item').length).toBe(0)
  })

  it('꺾쇠 목록에는 [AI 호출]이 없고 맨 아래가 [작업계획 생성]이다', async () => {
    const wrapper = mountActionBar('WP')
    await wrapper.get('.ab-split-caret').trigger('click')

    const items = wrapper.findAll('.ab-split-item')
    expect(items.some((n) => n.text().includes('AI 호출'))).toBe(false)
    expect(items[0].text()).toContain('멘트 복사')
    const last = items[items.length - 1]
    expect(last.text()).toContain('작업계획 생성')
    // 예전 [AI 호출]이 쓰던 그 자리다 — 가름선(--continuous)까지 물려받는다.
    expect(last.classes()).toContain('ab-split-item--continuous')
    expect(last.classes()).toContain('ab-split-item--main')
    await last.trigger('click')
    expect(wrapper.emitted('create-empty')).toBeTruthy()
  })

  it('진행할 수 없는 상태면 다른 주 단추와 똑같이 비활성이다', async () => {
    // [최종승인]·[시험 실행]과 같은 규칙을 쓴다 — 버튼이 사라지지 않고 눌리지 않게만 된다.
    const wrapper = mountActionBar('WP', false)
    const main = wrapper.get('[data-test="ab-create-work-plan"]')
    expect(main.attributes('disabled')).toBeDefined()
    expect(main.text()).toContain('작업계획 생성')
  })

  it('작업계획이 아닌 다음 단계는 예전 그대로다', async () => {
    const wrapper = mountActionBar('T')
    expect(wrapper.find('[data-test="ab-create-work-plan"]').exists()).toBe(false)
    await wrapper.get('.ab-dd-toggle').trigger('click')

    const items = wrapper.findAll('.ab-split-item')
    const last = items[items.length - 1]
    expect(last.text()).toContain('AI 호출')
    expect(last.classes()).toContain('ab-split-item--continuous')
    expect(items.some((n) => n.classes().includes('ab-split-item--main'))).toBe(false)
    expect(items.some((n) => n.text().includes('빈 문서 생성'))).toBe(true)
  })
})

describe('② [작업계획 생성]은 어디서 눌러도 같은 창을 연다', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
    getRequest.mockReset()
    getRequest.mockResolvedValue({ data: { providers: PROVIDERS, default_provider_id: 'aip_opus' } })
  })

  function seedNextAction(vm: any, typeCode: string) {
    vm.nextActionModalTabId = 'test.test.0002.0002-TS'
    vm.nextActionModalDocRef = 'test.test.0002.0001-R'
    vm.nextActionModalProjectId = 'test'
    vm.nextActionModalGroupId = 'test.test.0002'
    vm.nextActionModalModuleName = 'test'
    vm.nextActionModalTypeCode = typeCode
  }

  it('액션바·진행 목록의 [작업계획 생성](create-empty)도 전용 창을 연다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedNextAction(vm, 'WP')

    wrapper.findComponent({ name: 'NextActionModal' }).vm.$emit('create-empty')
    await wrapper.vm.$nextTick()

    // 예전에는 여기서 [+ 생성] 하나뿐인 WorkPlanCreateDialog 가 열렸다.
    expect(vm.workPlanProposalVisible).toBe(true)
    expect(vm.workPlanCreateVisible).toBe(false)
    expect(vm.nextEmptyDocModalVisible).toBe(false)
    expect(vm.workPlanCreateParentDocId).toBe('test.test.0002.0001-R')
    expect(vm.workPlanCreateGroupId).toBe('test.test.0002')
    wrapper.unmount()
  })

  it('작업계획이 아닌 타입은 지금까지대로 빈 문서 대화상자를 연다', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedNextAction(vm, 'N')

    wrapper.findComponent({ name: 'NextActionModal' }).vm.$emit('create-empty')
    await wrapper.vm.$nextTick()

    expect(vm.nextEmptyDocModalVisible).toBe(true)
    expect(vm.workPlanProposalVisible).toBe(false)
    expect(vm.workPlanCreateVisible).toBe(false)
    wrapper.unmount()
  })
})

describe('③ 공급자가 있는 창의 주버튼은 [AI 호출]이다', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
    getRequest.mockReset()
    postRequest.mockReset()
  })

  it('제목이 [작업계획 생성]이고 [메인작업] 표가 붙는다', async () => {
    const wrapper = await mountDialog()
    expect(wrapper.get('#wpp-title').text()).toContain('작업계획 생성')
    expect(wrapper.get('[data-test="wpp-main-task"]').text()).toBe('메인작업')
    wrapper.unmount()
  })

  it('파란 주버튼은 [AI 호출] 하나뿐이고 [문서생성]은 보조 버튼이다', async () => {
    const wrapper = await mountDialog()
    const create = wrapper.get('[data-test="wpp-create-empty"]')
    const copy = wrapper.get('[data-test="wpp-copy-mention"]')
    const ai = wrapper.get('[data-test="wpp-invoke-ai"]')

    expect(ai.text()).toContain('AI 호출')
    expect(ai.classes()).toContain('btn-primary')
    expect(ai.classes()).toContain('wpp-main-btn')
    expect(create.text()).toContain('문서생성')
    expect(create.classes()).toContain('btn-secondary')
    expect(create.classes()).not.toContain('btn-primary')
    expect(create.classes()).not.toContain('wpp-ft-last')
    expect(copy.classes()).toContain('btn-secondary')

    // 창 전체에 파란 주버튼은 하나다.
    expect(wrapper.findAll('.modal-ft .btn-primary').length).toBe(1)
    // 버튼 개수·순서는 예전 그대로다 — 취소 / 문서생성 / 멘트복사 / AI 호출.
    const labels = wrapper.findAll('.modal-ft .btn').map((n) => n.text().trim())
    expect(labels.length).toBe(4)
    expect(labels[1]).toContain('문서생성')
    expect(labels[2]).toContain('멘트복사')
    expect(labels[3]).toContain('AI 호출')
    wrapper.unmount()
  })

  it('창 어디에도 [맡길 단계]가 없다', async () => {
    const wrapper = await mountDialog()
    expect(wrapper.text()).not.toContain('맡길 단계')
    expect(wrapper.findAll('[data-test="wpp-step"]').length).toBe(0)
    wrapper.unmount()
  })

  it('세 로케일 모두 제목과 주버튼 이름이 바뀐다', async () => {
    for (const [locale, title, ai] of [
      ['ko', '작업계획 생성', 'AI 호출'],
      ['en', 'Create work plan', 'Call AI'],
      ['ja', '作業計画作成', 'AI 呼び出し'],
    ] as const) {
      i18n.global.locale.value = locale
      const wrapper = await mountDialog()
      expect(wrapper.get('#wpp-title').text()).toContain(title)
      expect(wrapper.get('[data-test="wpp-invoke-ai"]').text()).toContain(ai)
      expect(wrapper.get('[data-test="wpp-invoke-ai"]').classes()).toContain('btn-primary')
      wrapper.unmount()
    }
    i18n.global.locale.value = 'ko'
  })
})

describe('④ 고를 공급자가 없으면 그 칸도 [AI 호출]도 나오지 않는다', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
    getRequest.mockReset()
    postRequest.mockReset()
  })

  it('② 후보 공급자 칸이 통째로 없고 칸 번호는 1 하나뿐이다', async () => {
    const wrapper = await mountDialog([])
    expect(wrapper.find('[data-test="wpp-sec-providers"]').exists()).toBe(false)
    expect(wrapper.findAll('.wpp-sec').length).toBe(1)
    expect(wrapper.findAll('.wpp-sec-no').map((n: any) => n.text())).toEqual(['1'])
    expect(wrapper.text()).not.toContain('후보 공급자')
    wrapper.unmount()
  })

  it('[AI 호출]은 그리지 않고 [문서생성]이 맨 오른쪽 파란 주버튼이 된다', async () => {
    const wrapper = await mountDialog([])
    expect(wrapper.find('[data-test="wpp-invoke-ai"]').exists()).toBe(false)

    const create = wrapper.get('[data-test="wpp-create-empty"]')
    expect(create.classes()).toContain('btn-primary')
    expect(create.classes()).toContain('wpp-main-btn')
    // 맨 오른쪽으로 보내는 규칙(.wpp-ft-last { order: 9 })을 이 버튼만 단다.
    expect(create.classes()).toContain('wpp-ft-last')
    expect(wrapper.findAll('.modal-ft .btn').length).toBe(3)
    expect(wrapper.findAll('.modal-ft .btn-primary').length).toBe(1)
    wrapper.unmount()
  })

  it('타입 하나만 골라도 만들 수 있고 후보는 빈 배열로 나간다', async () => {
    postRequest.mockResolvedValue({
      data: { ok: true, doc_id: 'flowgate.default.0405.0009-WP', title: '작업계획', body: {} },
    })
    const wrapper = await mountDialog([])
    expect(wrapper.get('[data-test="wpp-create-empty"]').attributes('disabled')).toBeDefined()

    await wrapper.findAll('[data-test="wpp-type"]')[0].trigger('click')
    expect(wrapper.get('[data-test="wpp-create-empty"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-test="wpp-notice"]').text()).toContain('등록된 AI 공급자 없음')

    await wrapper.get('[data-test="wpp-create-empty"]').trigger('click')
    await flushPromises()
    const [url, body] = postRequest.mock.calls[0]
    expect(url).toBe('/api/v1/documents/work-plan')
    expect(body.provider_candidates).toEqual([])
    expect(body.quantities).toEqual({ DS: 1, T: 0 })
    wrapper.unmount()
  })

  it('[멘트복사]는 남아 있고 빈 공급자 목록을 실어 보낸다', async () => {
    const wrapper = await mountDialog([])
    await wrapper.findAll('[data-test="wpp-type"]')[0].trigger('click')
    await wrapper.get('[data-test="wpp-copy-mention"]').trigger('click')

    const scope = wrapper.emitted('copy-mention')![0][0] as any
    expect(scope.quantity_type_codes).toEqual(['DS'])
    expect(scope.provider_ids).toEqual([])
    wrapper.unmount()
  })

  it('세 로케일 모두 [AI 호출]이 없는 창이다', async () => {
    for (const [locale, create] of [
      ['ko', '문서생성'],
      ['en', 'Create document'],
      ['ja', '文書作成'],
    ] as const) {
      i18n.global.locale.value = locale
      const wrapper = await mountDialog([])
      expect(wrapper.find('[data-test="wpp-invoke-ai"]').exists()).toBe(false)
      const btn = wrapper.get('[data-test="wpp-create-empty"]')
      expect(btn.text()).toContain(create)
      expect(btn.classes()).toContain('btn-primary')
      wrapper.unmount()
    }
    i18n.global.locale.value = 'ko'
  })
})
