// 다음 액션이 작업계획(WP)일 때의 전용 제안 다이얼로그 — flowgate.default.0405 T0007.
//
// P0004 가 못 박은 화면 계약을 시험한다.
//   · 진행 목록 대신 전용 창이 열리고, WP 가 아니면 지금까지와 똑같이 진행 목록이 열린다
//   · 두 칸이 하나의 범위를 만들고 네 버튼이 그것을 그대로 나른다
//   · 버튼 네 개는 어떤 상태에서도 개수와 자리를 바꾸지 않고 사유 한 줄만 바뀐다
//
// 0405 T0011 rev1 (반려 "맡길 단계??? 이건 대체 왜나와"): 단계를 고르는 칸이 없어졌다.
// 이 파일은 그 칸이 되살아나지 않는 것까지 함께 못 박는다.
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import WorkPlanProposalDialog from '@main/components/WorkPlanProposalDialog.vue'
import { useDocTypeStore } from '@main/stores/docTypeStore'
import { useAiProviderStore } from '@main/stores/aiProvider'

const postRequest = vi.fn()
const getRequest = vi.fn()

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: (...args: any[]) => getRequest(...args),
  postRequest: (...args: any[]) => postRequest(...args),
  patchRequest: vi.fn(),
}))

const TYPES = [
  { id: 21, code: 'DS', label: '설계지시', category: 'design', countable: true, unit: 'sheet' },
  { id: 22, code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet' },
  { id: 32, code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR' },
  { id: 33, code: 'TS', label: '시험지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TSR' },
  { id: 34, code: 'TR', label: '작업레포트', category: 'instruction', countable: false, unit: null },
  { id: 35, code: 'TSR', label: '시험결과', category: 'instruction', countable: false, unit: null },
  { id: 41, code: 'WP', label: '작업계획', category: 'plan', countable: false, unit: null },
]

const PROVIDERS = [
  { id: 'aip_opus', name: 'Claude Opus 5', kind: 'claude', exec_type: 'cli' },
  { id: 'aip_sonnet', name: 'Claude Sonnet 5', kind: 'claude', exec_type: 'cli' },
]

function seedStores() {
  const docTypeStore = useDocTypeStore()
  docTypeStore.items = TYPES as any
  docTypeStore.labelMap = Object.fromEntries(TYPES.map((item) => [item.code, item.label]))
  const providerStore = useAiProviderStore()
  providerStore.providers = PROVIDERS as any
  providerStore.loadedProjectId = 'flowgate'
}

async function mountDialog(props: Record<string, unknown> = {}) {
  seedStores()
  const wrapper = mount(WorkPlanProposalDialog, {
    props: {
      visible: true,
      parentDocId: 'flowgate.default.0405.0001-R',
      projectId: 'flowgate',
      groupId: 'flowgate.default.0405',
      ...props,
    },
    global: { plugins: [i18n], stubs: { teleport: true, AppIcon: true } },
  })
  await flushPromises()
  return wrapper
}

function rows(wrapper: any, kind: string) {
  return wrapper.findAll(`[data-test="wpp-${kind}"]`)
}

async function pick(wrapper: any, kind: string, index: number) {
  await rows(wrapper, kind)[index].trigger('click')
}

describe('WorkPlanProposalDialog — 두 칸과 네 버튼', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
    postRequest.mockReset()
    getRequest.mockReset()
    getRequest.mockResolvedValue({ data: { providers: PROVIDERS, default_provider_id: 'aip_opus' } })
  })

  it('네 버튼이 언제나 같은 개수로 그려진다', async () => {
    const wrapper = await mountDialog()
    for (const key of ['cancel', 'create-empty', 'copy-mention', 'invoke-ai']) {
      expect(wrapper.find(`[data-test="wpp-${key}"]`).exists()).toBe(true)
    }
    // 아무것도 고르지 않은 상태에서도 사라지지 않는다 — 비활성일 뿐이다.
    expect(wrapper.find('[data-test="wpp-create-empty"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="wpp-copy-mention"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="wpp-invoke-ai"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="wpp-cancel"]').attributes('disabled')).toBeUndefined()
  })

  it('두 칸은 처음에 아무것도 고르지 않은 상태다', async () => {
    const wrapper = await mountDialog()
    expect(rows(wrapper, 'type').length).toBe(4)
    expect(rows(wrapper, 'type').filter((row: any) => row.classes('on')).length).toBe(0)
    expect(rows(wrapper, 'provider').filter((row: any) => row.classes('on')).length).toBe(0)
  })

  it('① [전체]/[해제]는 타입 전체와 서버 등록 순서의 scope를 만든다', async () => {
    const wrapper = await mountDialog()

    await wrapper.get('[data-test="wpp-select-all-types"]').trigger('click')
    expect(rows(wrapper, 'type').filter((row: any) => row.classes('on')).length).toBe(4)
    expect(wrapper.findAll('.wpp-count-pill')[0].text()).toBe('4 / 4')

    await pick(wrapper, 'provider', 0)
    await wrapper.get('[data-test="wpp-copy-mention"]').trigger('click')
    const scope = wrapper.emitted('copy-mention')![0][0] as any
    expect(scope.quantity_type_codes).toEqual(['DS', 'D', 'T', 'TS'])

    await wrapper.get('[data-test="wpp-clear-all-types"]').trigger('click')
    expect(rows(wrapper, 'type').filter((row: any) => row.classes('on')).length).toBe(0)
    expect(wrapper.findAll('.wpp-count-pill')[0].text()).toBe('0 / 4')
    expect(wrapper.get('[data-test="wpp-notice"]').text()).toContain('장수를 하나도 고르지 않았습니다')
  })

  it('② [전체]/[해제]는 공급자 전체와 서버 등록 순서의 scope를 만든다', async () => {
    const wrapper = await mountDialog()

    await pick(wrapper, 'type', 0)
    await wrapper.get('[data-test="wpp-select-all-providers"]').trigger('click')
    expect(rows(wrapper, 'provider').filter((row: any) => row.classes('on')).length).toBe(2)
    expect(wrapper.findAll('.wpp-count-pill')[1].text()).toBe('2 / 2')

    await wrapper.get('[data-test="wpp-copy-mention"]').trigger('click')
    const scope = wrapper.emitted('copy-mention')![0][0] as any
    expect(scope.provider_ids).toEqual(['aip_opus', 'aip_sonnet'])

    await wrapper.get('[data-test="wpp-clear-all-providers"]').trigger('click')
    expect(rows(wrapper, 'provider').filter((row: any) => row.classes('on')).length).toBe(0)
    expect(wrapper.findAll('.wpp-count-pill')[1].text()).toBe('0 / 2')
    expect(wrapper.get('[data-test="wpp-notice"]').text()).toContain('공급자를 하나도 고르지 않았습니다')
  })

  it('ko/en/ja에서 전체선택/해제 문안을 실제 문자열로 제공한다', () => {
    const cases = [
      ['ko', '전체', '해제'],
      ['en', 'Select all', 'Clear'],
      ['ja', '全て', '解除'],
    ] as const

    for (const [locale, selectAll, clearAll] of cases) {
      i18n.global.locale.value = locale
      expect(i18n.global.t('main.work_plan_proposal_dialog.select_all')).toBe(selectAll)
      expect(i18n.global.t('main.work_plan_proposal_dialog.clear_all')).toBe(clearAll)
    }
    i18n.global.locale.value = 'ko'
  })

  it('맡길 단계를 고르는 칸은 창에 없다', async () => {
    const wrapper = await mountDialog()
    await pick(wrapper, 'type', 2)   // T (set) — 예전에는 여기서 단계 후보가 펼쳐졌다
    await pick(wrapper, 'type', 3)   // TS (set)
    await flushPromises()
    expect(rows(wrapper, 'step').length).toBe(0)
    expect(wrapper.text()).not.toContain('맡길 단계')
    // 칸이 둘이므로 번호도 둘까지다.
    expect(wrapper.findAll('.wpp-sec').length).toBe(2)
    expect(wrapper.findAll('.wpp-sec-no').map((n: any) => n.text())).toEqual(['1', '2'])
  })

  it('사유 줄에도 맡길 단계 수가 나오지 않는다', async () => {
    const wrapper = await mountDialog()
    await pick(wrapper, 'type', 0)
    await pick(wrapper, 'provider', 0)
    const notice = wrapper.find('[data-test="wpp-notice"]').text()
    expect(notice).not.toContain('맡길 단계')
    expect(notice).toContain('공급자')
  })

  it('[멘트복사]는 두 배열을 서버 등록 순서로 담아 올린다', async () => {
    const wrapper = await mountDialog()
    await pick(wrapper, 'type', 2)      // T
    await pick(wrapper, 'type', 0)      // DS — 나중에 골라도 순서는 등록 순서
    await pick(wrapper, 'provider', 1)  // sonnet
    await pick(wrapper, 'provider', 0)  // opus
    await wrapper.find('[data-test="wpp-copy-mention"]').trigger('click')

    const scope = wrapper.emitted('copy-mention')![0][0] as any
    expect(scope.quantity_type_codes).toEqual(['DS', 'T'])
    expect(scope.step_keys).toBeUndefined()
    expect(scope.provider_ids).toEqual(['aip_opus', 'aip_sonnet'])
  })

  it('[AI 호출]은 같은 범위와 첫 번째 공급자를 함께 올린다', async () => {
    const wrapper = await mountDialog()
    await pick(wrapper, 'type', 0)
    await pick(wrapper, 'provider', 1)
    await pick(wrapper, 'provider', 0)
    await wrapper.find('[data-test="wpp-invoke-ai"]').trigger('click')

    const payload = wrapper.emitted('invoke-ai')![0][0] as any
    expect(payload.providerId).toBe('aip_opus')
    expect(payload.scope.quantity_type_codes).toEqual(['DS'])
  })

  it('[문서생성]은 고른 타입만 1, 나머지는 0 으로 보낸다', async () => {
    postRequest.mockResolvedValue({
      data: { ok: true, doc_id: 'flowgate.default.0405.0008-WP', title: '작업계획', body: {} },
    })
    const wrapper = await mountDialog()
    await pick(wrapper, 'type', 0)      // DS
    await pick(wrapper, 'type', 2)      // T
    await pick(wrapper, 'provider', 0)
    await wrapper.find('[data-test="wpp-create-empty"]').trigger('click')
    await flushPromises()

    const [url, body] = postRequest.mock.calls[0]
    expect(url).toBe('/api/v1/documents/work-plan')
    expect(body.counted_types).toEqual(['DS', 'D', 'T', 'TS'])
    expect(body.quantities).toEqual({ DS: 1, D: 0, T: 1, TS: 0 })
    expect(body.provider_candidates).toEqual(['aip_opus'])
    expect(body.step_keys).toBeUndefined()
    expect(wrapper.emitted('created')).toBeTruthy()
    expect(wrapper.emitted('update:visible')![0]).toEqual([false])
  })

  it('생성이 실패하면 창은 열린 채 사유 한 줄만 바뀐다', async () => {
    postRequest.mockRejectedValue({ response: { data: { message: '작업계획을 만들지 못했습니다.' } } })
    const wrapper = await mountDialog()
    await pick(wrapper, 'type', 0)
    await pick(wrapper, 'provider', 0)
    await wrapper.find('[data-test="wpp-create-empty"]').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('update:visible')).toBeFalsy()
    expect(wrapper.find('[data-test="wpp-notice"]').text()).toContain('작업계획을 만들지 못했습니다.')
    expect(wrapper.find('[data-test="wpp-invoke-ai"]').exists()).toBe(true)
  })

  it('사유가 없으면 안내 한 줄에 고른 내용 요약이 남는다', async () => {
    const wrapper = await mountDialog()
    await pick(wrapper, 'type', 0)
    await pick(wrapper, 'provider', 0)
    const notice = wrapper.find('[data-test="wpp-notice"]')
    expect(notice.text()).toContain('설계')
    expect(notice.classes('warn')).toBe(false)
  })

  it('다른 AI 실행이 돌고 있으면 [AI 호출]만 비활성이다', async () => {
    const wrapper = await mountDialog({ aiActive: true })
    await pick(wrapper, 'type', 0)
    await pick(wrapper, 'provider', 0)
    expect(wrapper.find('[data-test="wpp-invoke-ai"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="wpp-copy-mention"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('[data-test="wpp-create-empty"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('[data-test="wpp-cancel"]').attributes('disabled')).toBeUndefined()
  })

  it('부모가 도는 동안에도 버튼은 자리를 지키고 사유만 바뀐다', async () => {
    const wrapper = await mountDialog({ busyAction: 'copy' })
    await pick(wrapper, 'type', 0)
    await pick(wrapper, 'provider', 0)
    expect(wrapper.findAll('.modal-ft .btn').length).toBe(4)
    expect(wrapper.find('[data-test="wpp-notice"]').text()).toContain('발급')
  })

  it('[취소]는 요청을 하나도 보내지 않고 닫는다', async () => {
    const wrapper = await mountDialog()
    await pick(wrapper, 'type', 0)
    await wrapper.find('[data-test="wpp-cancel"]').trigger('click')
    expect(postRequest).not.toHaveBeenCalled()
    expect(wrapper.emitted('update:visible')![0]).toEqual([false])
  })

  it('일괄 선택했어도 다시 열면 두 칸이 초기 상태로 돌아온다', async () => {
    const wrapper = await mountDialog()
    await wrapper.get('[data-test="wpp-select-all-types"]').trigger('click')
    await wrapper.get('[data-test="wpp-select-all-providers"]').trigger('click')
    expect(rows(wrapper, 'type').every((row: any) => row.classes('on'))).toBe(true)
    expect(rows(wrapper, 'provider').every((row: any) => row.classes('on'))).toBe(true)

    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })
    await flushPromises()
    expect(rows(wrapper, 'type').filter((row: any) => row.classes('on')).length).toBe(0)
    expect(rows(wrapper, 'provider').filter((row: any) => row.classes('on')).length).toBe(0)
  })
})

// 0405 T0011 rev2 — 반려: "AI공급자 선택할게 없으면 [2 후보공급자]는 안나오게 하고 1만
// 선택하고 생성할수 있게 해야하지 않겠니?" / "AI공급자 선택할게 없으면 [AI호출이 의미
// 없잖아] [+ 문서생성] 이 맨 우측으로 오게하고 이걸 강조해야지"
describe('WorkPlanProposalDialog — 고를 공급자가 하나도 없을 때', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
    postRequest.mockReset()
    getRequest.mockReset()
    getRequest.mockResolvedValue({ data: { providers: [], default_provider_id: null } })
  })

  /** 목록을 아직 한 번도 받지 않은 상태에서 여는 창. */
  function mountUnloaded() {
    const docTypeStore = useDocTypeStore()
    docTypeStore.items = TYPES as any
    docTypeStore.labelMap = Object.fromEntries(TYPES.map((item) => [item.code, item.label]))
    return mount(WorkPlanProposalDialog, {
      props: {
        visible: true,
        parentDocId: 'flowgate.default.0405.0001-R',
        projectId: 'flowgate',
        groupId: 'flowgate.default.0405',
      },
      global: { plugins: [i18n], stubs: { teleport: true, AppIcon: true } },
    })
  }

  async function mountNoProviders() {
    const wrapper = mountUnloaded()
    await flushPromises()
    return wrapper
  }

  it('답이 오기 전에는 아무 버튼도 그리지 않는다 — 그려진 뒤에는 움직이지 않는다', async () => {
    let release: (value: any) => void = () => {}
    getRequest.mockReturnValue(new Promise((resolve) => { release = resolve }))
    const wrapper = mountUnloaded()
    await wrapper.vm.$nextTick()

    // 공급자 개수가 이 창의 모양을 정한다. 답이 오기 전에 그리면 버튼이 한 번 자리를 옮긴다.
    expect(wrapper.find('[data-test="wpp-loading"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="wpp-create-empty"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="wpp-invoke-ai"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="wpp-cancel"]').exists()).toBe(true)

    release({ data: { providers: [], default_provider_id: null } })
    await flushPromises()
    expect(wrapper.find('[data-test="wpp-loading"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="wpp-create-empty"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('② 후보 공급자 칸을 그리지 않는다', async () => {
    const wrapper = await mountNoProviders()
    expect(wrapper.find('[data-test="wpp-sec-providers"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-test="wpp-provider"]').length).toBe(0)
    expect(wrapper.findAll('.wpp-sec').length).toBe(1)
    expect(wrapper.text()).not.toContain('후보 공급자')
    // ① 칸은 그대로 고를 수 있고 일괄 선택/해제도 남는다. ② 액션은 칸과 함께 사라진다.
    expect(wrapper.findAll('[data-test="wpp-type"]').length).toBe(4)
    expect(wrapper.find('[data-test="wpp-select-all-types"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="wpp-clear-all-types"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="wpp-select-all-providers"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="wpp-clear-all-providers"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('[AI 호출]이 없고 [문서생성]이 맨 오른쪽 주버튼이다', async () => {
    const wrapper = await mountNoProviders()
    expect(wrapper.find('[data-test="wpp-invoke-ai"]').exists()).toBe(false)
    const create = wrapper.get('[data-test="wpp-create-empty"]')
    expect(create.classes()).toContain('btn-primary')
    expect(create.classes()).toContain('wpp-ft-last')
    expect(wrapper.get('[data-test="wpp-copy-mention"]').classes()).toContain('btn-secondary')
    expect(wrapper.findAll('.modal-ft .btn').length).toBe(3)
    wrapper.unmount()
  })

  it('① 칸만 골라도 만들 수 있다 — 사유 줄이 공급자를 요구하지 않는다', async () => {
    const wrapper = await mountNoProviders()
    expect(wrapper.get('[data-test="wpp-notice"]').text()).toContain('장수를 하나도 고르지 않았습니다')

    await wrapper.get('[data-test="wpp-select-all-types"]').trigger('click')
    expect(rows(wrapper, 'type').filter((row: any) => row.classes('on')).length).toBe(4)
    const notice = wrapper.get('[data-test="wpp-notice"]')
    expect(notice.text()).not.toContain('공급자를 하나도 고르지 않았습니다')
    expect(notice.text()).toContain('등록된 AI 공급자 없음')
    expect(notice.classes('warn')).toBe(false)
    expect(wrapper.get('[data-test="wpp-create-empty"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-test="wpp-copy-mention"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('빈 후보 목록을 그대로 실어 보낸다', async () => {
    postRequest.mockResolvedValue({
      data: { ok: true, doc_id: 'flowgate.default.0405.0010-WP', title: '작업계획', body: {} },
    })
    const wrapper = await mountNoProviders()
    await pick(wrapper, 'type', 0)
    await pick(wrapper, 'type', 2)
    await wrapper.get('[data-test="wpp-create-empty"]').trigger('click')
    await flushPromises()

    const [, body] = postRequest.mock.calls[0]
    expect(body.provider_candidates).toEqual([])
    expect(body.quantities).toEqual({ DS: 1, D: 0, T: 1, TS: 0 })
    wrapper.unmount()
  })

  it('다른 AI 실행이 돌고 있어도 사유 줄에 그 말이 나오지 않는다', async () => {
    const wrapper = await mountNoProviders()
    await wrapper.setProps({ aiActive: true })
    await pick(wrapper, 'type', 0)
    // [AI 호출]이 없는 창이다 — 그 버튼의 사유는 이 창의 일이 아니다.
    expect(wrapper.get('[data-test="wpp-notice"]').text()).not.toContain('다른 AI 실행')
    expect(wrapper.get('[data-test="wpp-create-empty"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('목록을 읽지 못한 것은 "없다"가 아니다 — 칸을 남기고 다시 시도를 준다', async () => {
    getRequest.mockRejectedValue(new Error('boom'))
    const wrapper = await mountNoProviders()
    expect(wrapper.find('[data-test="wpp-sec-providers"]').exists()).toBe(true)
    expect(wrapper.find('.wpp-load-error').exists()).toBe(true)
    expect(wrapper.find('[data-test="wpp-invoke-ai"]').exists()).toBe(true)
    wrapper.unmount()
  })
})
