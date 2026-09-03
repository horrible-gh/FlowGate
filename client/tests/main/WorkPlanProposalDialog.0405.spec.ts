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
  extractApiErrorMessage: (error: any, fallback: string) =>
    error?.response?.data?.detail ?? error?.response?.data?.error?.message ?? fallback,
}))

// 0429 T0004 (NR0003): `data`/`items` mirrors the real document-types order — design
// series sorts before instruction, so D precedes DS, and DS's real series is
// 'instruction' (not 'design'). This used to list DS first under a 'design' category,
// quietly assuming the very ordering contract the bug broke.
const TYPES = [
  { id: 22, code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet' },
  { id: 21, code: 'DS', label: '설계지시', category: 'instruction', countable: true, unit: 'sheet' },
  { id: 32, code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR' },
  { id: 33, code: 'TS', label: '시험지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TSR' },
  { id: 34, code: 'TR', label: '작업레포트', category: 'instruction', countable: false, unit: null },
  { id: 35, code: 'TSR', label: '시험결과', category: 'instruction', countable: false, unit: null },
  { id: 41, code: 'WP', label: '작업계획', category: 'plan', countable: false, unit: null },
]

// The work-plan registry's own canonical order (DS leads the design series) — the
// additive `work_plan_countable_types` field, separate from the raw `items` order above.
const TYPES_WP = [
  { code: 'DS', label: '설계지시', category: 'instruction', unit: 'sheet' },
  { code: 'D', label: '기본설계', category: 'design', unit: 'sheet' },
  { code: 'T', label: '작업지시', category: 'instruction', unit: 'set', pair_code: 'TR' },
  { code: 'TS', label: '시험지시', category: 'instruction', unit: 'set', pair_code: 'TSR' },
]

const PROVIDERS = [
  { id: 'aip_opus', name: 'Claude Opus 5', kind: 'claude', exec_type: 'cli' },
  { id: 'aip_sonnet', name: 'Claude Sonnet 5', kind: 'claude', exec_type: 'cli' },
]

function seedStores() {
  const docTypeStore = useDocTypeStore()
  docTypeStore.items = TYPES as any
  docTypeStore.labelMap = Object.fromEntries(TYPES.map((item) => [item.code, item.label]))
  // 0429 T0004: seed the work-plan canonical registry directly too, the same way this
  // helper already seeds `items`/`labelMap` instead of mocking the HTTP response — this
  // is what makes docTypeStore.countableTypes (and the scope this dialog builds from it)
  // use the DS-leads server order rather than falling back to `items`' raw order.
  docTypeStore.workPlanCountableTypes = TYPES_WP as any
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
    // flowgate.default.0416 TR0005 rev2: 실행 프로바이더는 aiProviderStore 의 값이고, 그
    // store 는 고른 값을 localStorage 에 저장한다(앱과 같은 계약). 지우지 않으면 앞 시험이
    // 고른 값이 다음 시험의 기본값을 이긴다.
    localStorage.clear()
    postRequest.mockReset()
    getRequest.mockReset()
    getRequest.mockResolvedValue({ data: { providers: PROVIDERS, default_provider_id: 'aip_opus' } })
  })

  it('passes the active UI locale to loadLabels when reopened', async () => {
    i18n.global.locale.value = 'en'
    const loadLabels = vi.spyOn(useDocTypeStore(), 'loadLabels')
    const wrapper = await mountDialog()

    expect(loadLabels).toHaveBeenLastCalledWith('en')
    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })
    await flushPromises()
    expect(loadLabels).toHaveBeenLastCalledWith('en')
    expect(loadLabels).toHaveBeenCalledTimes(2)
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

  it('[AI 호출]은 같은 범위와 실행 프로바이더 박스의 값을 함께 올린다', async () => {
    const wrapper = await mountDialog()
    await pick(wrapper, 'type', 0)
    await pick(wrapper, 'provider', 1)
    await pick(wrapper, 'provider', 0)
    await wrapper.find('[data-test="wpp-invoke-ai"]').trigger('click')

    const payload = wrapper.emitted('invoke-ai')![0][0] as any
    // flowgate.default.0416 TR0005 rev2: 실행 프로바이더는 앱 공통 선택(aiProviderStore)의
    // 값이다 — 여기서는 서버가 준 default_provider_id 가 그 값이고, 후보(② 칸) 선택
    // 순서와는 무관하다.
    expect(payload.providerId).toBe('aip_opus')
    expect(payload.scope.provider_id).toBe('aip_opus')
    expect(payload.scope.quantity_type_codes).toEqual(['DS'])
  })

  it('[문서생성]은 고른 타입을 quantities 에서 빼고, 나머지만 0 으로 보낸다', async () => {
    // flowgate.default.0423 T0005 item 11: a checked type used to be hardcoded to 1.
    // Now it is simply absent from quantities, so the server fills it from the
    // group's workflow_type_counts derivation (or 0 when there is none) instead of a
    // guessed 1 — only an unchecked type still forces an explicit 0.
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
    expect(body.quantities).toEqual({ D: 0, TS: 0 })
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

  it('다른 AI 실행이 돌고 있으면 변경 버튼 세 개가 모두 비활성이다', async () => {
    const wrapper = await mountDialog({ aiActive: true })
    await pick(wrapper, 'type', 0)
    await pick(wrapper, 'provider', 0)
    expect(wrapper.find('[data-test="wpp-invoke-ai"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="wpp-copy-mention"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="wpp-create-empty"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="wpp-cancel"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('[data-test="wpp-notice"]').text()).toContain('다른 AI 실행')
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

  // flowgate.default.0416 T0004 (B0001 "플래너한테 아무런 멘트도 전달할수가 없는거지?"):
  // 작업계획 전체 단계에 공통으로 붙는 플래너 멘트 입력. 문서생성·멘트복사·AI호출 세
  // 경로가 같은 값을 나른다.
  describe('플래너 멘트', () => {
    it('처음엔 비어 있고, 다시 열면 초기화된다', async () => {
      const wrapper = await mountDialog()
      const input = wrapper.get('[data-test="wpp-note"]')
      expect((input.element as HTMLInputElement).value).toBe('')

      await input.setValue('기존 스타일을 따를 것')
      expect((wrapper.get('[data-test="wpp-note"]').element as HTMLInputElement).value)
        .toBe('기존 스타일을 따를 것')

      await wrapper.setProps({ visible: false })
      await wrapper.setProps({ visible: true })
      await flushPromises()
      expect((wrapper.get('[data-test="wpp-note"]').element as HTMLInputElement).value).toBe('')
    })

    it('[문서생성]이 입력한 멘트를 defaults.note 로 그대로 보낸다', async () => {
      postRequest.mockResolvedValue({
        data: { ok: true, doc_id: 'flowgate.default.0405.0009-WP', title: '작업계획', body: {} },
      })
      const wrapper = await mountDialog()
      await pick(wrapper, 'type', 0)
      await pick(wrapper, 'provider', 0)
      await wrapper.get('[data-test="wpp-note"]').setValue('한 줄 지시')
      await wrapper.find('[data-test="wpp-create-empty"]').trigger('click')
      await flushPromises()

      const [, body] = postRequest.mock.calls[0]
      // flowgate.default.0416 TR0005 rev2: defaults.provider_id 는 null 로 남는다. 이 값은
      // 서버에서 만들어지는 모든 단계의 provider_id 로 그대로 번지는데, 단계 배정은 T0004
      // 작업 3 이 생성 후 WorkPlanEditor.vue 의 책임으로 못 박은 일이다.
      expect(body.defaults).toEqual({ provider_id: null, note: '한 줄 지시' })
    })

    it('[멘트복사]와 [AI 호출]이 같은 scope.note 를 올린다', async () => {
      const wrapper = await mountDialog()
      await pick(wrapper, 'type', 0)
      await pick(wrapper, 'provider', 0)
      await wrapper.get('[data-test="wpp-note"]').setValue('공통 지시')

      await wrapper.find('[data-test="wpp-copy-mention"]').trigger('click')
      expect((wrapper.emitted('copy-mention')![0][0] as any).note).toBe('공통 지시')

      await wrapper.find('[data-test="wpp-invoke-ai"]').trigger('click')
      expect((wrapper.emitted('invoke-ai')![0][0] as any).scope.note).toBe('공통 지시')
    })

    it('상한을 넘긴 멘트는 세 버튼을 모두 막고 사유를 보여준다', async () => {
      const wrapper = await mountDialog()
      await pick(wrapper, 'type', 0)
      await pick(wrapper, 'provider', 0)
      await wrapper.get('[data-test="wpp-note"]').setValue('가'.repeat(1001))

      expect(wrapper.find('[data-test="wpp-create-empty"]').attributes('disabled')).toBeDefined()
      expect(wrapper.find('[data-test="wpp-copy-mention"]').attributes('disabled')).toBeDefined()
      expect(wrapper.find('[data-test="wpp-invoke-ai"]').attributes('disabled')).toBeDefined()
      expect(wrapper.get('[data-test="wpp-notice"]').text()).toContain('전달 멘트가 글자 수 제한을 초과했습니다.')
      expect(wrapper.get('[data-test="wpp-note"]').classes()).toContain('is-over-limit')
    })
  })

  // flowgate.default.0416 TR0005 (반려: "[실행 프로바이더] 이거 어디갔냐고" /
  // "\"전달멘트\" 랑 박스 높낮이는 하나도 안맞고"): 이번 실행에 쓸 프로바이더를 고르는 박스.
  // 후보 다중선택(② 칸)과는 독립된 값이고, 전달 멘트와 한 행에 나란히 그려진다.
  //
  // rev2 (검토 발견 1): 이 박스의 값은 이 창의 것이 아니라 앱 공통 선택(aiProviderStore)이다.
  // rev1 은 로컬 ref 를 providersLoaded[0] 으로 채웠던 탓에, 헤더·AI 호출 창이 A 를 보여
  // 주는데 이 창만 B 를 보여 주고 B 로 실행할 수 있었다. 아래 시험들이 그 공유를 못 박는다.
  describe('실행 프로바이더', () => {
    it('앱 공통 선택(aiProviderStore)의 값을 그대로 보여준다 — 목록 첫 값이 아니다', async () => {
      // 서버가 정한 기본 공급자가 목록의 첫 값과 다른 상황. rev1 은 여기서 첫 값(opus)을
      // 보여 주며 헤더·AI 호출 창과 어긋났다.
      getRequest.mockResolvedValue({ data: { providers: PROVIDERS, default_provider_id: 'aip_sonnet' } })
      const wrapper = await mountDialog()
      const select = wrapper.get('[data-test="wpp-default-provider"] select')
      expect((select.element as HTMLSelectElement).value).toBe('aip_sonnet')
      expect(useAiProviderStore().selectedProviderId).toBe('aip_sonnet')
    })

    it('여기서 고른 값이 앱 공통 선택에 그대로 되쓰인다', async () => {
      const wrapper = await mountDialog()
      await wrapper.get('[data-test="wpp-default-provider"] select').setValue('aip_sonnet')
      // 다른 다이얼로그(AiInvokeDialog.vue:44)와 같은 계약 — store 가 정본이다.
      expect(useAiProviderStore().selectedProviderId).toBe('aip_sonnet')
    })

    it('전달 멘트 입력과 한 행(.wpp-note-row)에 나란히 있다', async () => {
      const wrapper = await mountDialog()
      const row = wrapper.get('.wpp-note-row')
      expect(row.find('[data-test="wpp-default-provider"]').exists()).toBe(true)
      expect(row.find('[data-test="wpp-note"]').exists()).toBe(true)
    })

    it('다시 고르면 [멘트복사]·[AI 호출]이 새 값을 나르고, [문서생성]은 단계를 배정하지 않는다', async () => {
      postRequest.mockResolvedValue({
        data: { ok: true, doc_id: 'flowgate.default.0416.0012-WP', title: '작업계획', body: {} },
      })
      const wrapper = await mountDialog()
      await pick(wrapper, 'type', 0)
      await pick(wrapper, 'provider', 0)
      await wrapper.get('[data-test="wpp-default-provider"] select').setValue('aip_sonnet')

      await wrapper.get('[data-test="wpp-copy-mention"]').trigger('click')
      expect((wrapper.emitted('copy-mention')![0][0] as any).provider_id).toBe('aip_sonnet')

      await wrapper.get('[data-test="wpp-invoke-ai"]').trigger('click')
      const aiPayload = wrapper.emitted('invoke-ai')![0][0] as any
      expect(aiPayload.providerId).toBe('aip_sonnet')
      expect(aiPayload.scope.provider_id).toBe('aip_sonnet')

      await wrapper.find('[data-test="wpp-create-empty"]').trigger('click')
      await flushPromises()
      const [, body] = postRequest.mock.calls[0]
      // 발견 2: 이 값이 defaults.provider_id 로 나가면 만들어지는 모든 단계가 사람 몰래
      // 그 공급자로 배정된다. 실행 프로바이더는 AI 를 실제로 돌리는 두 경로만 나른다.
      expect(body.defaults.provider_id).toBeNull()
    })

    it('다시 열어도 고른 값이 남는다 — 창의 값이 아니라 앱 공통 선택이기 때문이다', async () => {
      const wrapper = await mountDialog()
      await wrapper.get('[data-test="wpp-default-provider"] select').setValue('aip_sonnet')
      expect((wrapper.get('[data-test="wpp-default-provider"] select').element as HTMLSelectElement).value)
        .toBe('aip_sonnet')

      await wrapper.setProps({ visible: false })
      await wrapper.setProps({ visible: true })
      await flushPromises()
      expect((wrapper.get('[data-test="wpp-default-provider"] select').element as HTMLSelectElement).value)
        .toBe('aip_sonnet')
    })

    // 발견 5: 옆의 전달 멘트 입력은 :disabled 로 잠그는데 이 박스만 이벤트를 무시했다.
    // 그러면 실행 중에 고른 값이 화면에는 남고 페이로드에는 안 들어가 둘이 갈라진다.
    it('실행 중에는 옆 입력과 똑같이 잠긴다', async () => {
      const wrapper = await mountDialog({ busyAction: 'ai' })
      expect(wrapper.get('[data-test="wpp-default-provider"] select').attributes('disabled'))
        .toBeDefined()
      expect(wrapper.get('[data-test="wpp-note"]').attributes('disabled')).toBeDefined()
    })

    it('후보 다중선택(② 칸)과는 독립적이다 — 후보를 안 골라도 값이 있다', async () => {
      const wrapper = await mountDialog()
      expect(rows(wrapper, 'provider').filter((row: any) => row.classes('on')).length).toBe(0)
      expect((wrapper.get('[data-test="wpp-default-provider"] select').element as HTMLSelectElement).value)
        .toBe('aip_opus')
    })
  })

  // flowgate.default.0416 TR0005 rev2 (검토 발견 6): 멘트 글자 수 상한은 서버가 정본이다
  // (0406 T0022). WorkPlanEditor.vue:541 / WorkflowDecisionModal.vue:1132 와 같은 값을 읽고,
  // 못 읽으면 기존 기본값 1000 으로 남는다.
  describe('멘트 글자 수 상한', () => {
    it('서버가 준 note_max_chars 를 쓴다 — 하드코딩된 1000 이 아니다', async () => {
      getRequest.mockImplementation((url: string) => {
        if (url === '/api/v1/workflow/sequence') {
          return Promise.resolve({ data: { note_max_chars: 12, items: [] } })
        }
        return Promise.resolve({ data: { providers: PROVIDERS, default_provider_id: 'aip_opus' } })
      })
      const wrapper = await mountDialog()
      await pick(wrapper, 'type', 0)
      await pick(wrapper, 'provider', 0)
      await wrapper.get('[data-test="wpp-note"]').setValue('가'.repeat(13))
      await flushPromises()

      expect(wrapper.get('[data-test="wpp-note-count"]').text()).toContain('12')
      expect(wrapper.find('[data-test="wpp-create-empty"]').attributes('disabled')).toBeDefined()
      expect(wrapper.get('[data-test="wpp-notice"]').text())
        .toContain('전달 멘트가 글자 수 제한을 초과했습니다.')
    })

    it('상한을 못 읽으면 기존 기본값 1000 으로 남는다', async () => {
      getRequest.mockImplementation((url: string) => {
        if (url === '/api/v1/workflow/sequence') return Promise.reject(new Error('boom'))
        return Promise.resolve({ data: { providers: PROVIDERS, default_provider_id: 'aip_opus' } })
      })
      const wrapper = await mountDialog()
      await pick(wrapper, 'type', 0)
      await pick(wrapper, 'provider', 0)
      await wrapper.get('[data-test="wpp-note"]').setValue('가'.repeat(1000))
      await flushPromises()

      expect(wrapper.find('[data-test="wpp-create-empty"]').attributes('disabled')).toBeUndefined()
    })
  })

  // flowgate.default.0421 NR0003/T0005 — 서버(get_workflow_sequence)는 이미 시퀀스 머리 행의
  // note를 싣고 있다. 이 창은 그 값을 꺼내 쓰지 않고 버려 왔다 — 저장이 아니라 표시가
  // 문제였다는 것을 값 있는 프리필로 못 박는다.
  describe('전달 멘트 프리필', () => {
    it('시퀀스 머리 행에 note가 있으면 열자마자 채워지고 자동 채움 안내가 뜬다', async () => {
      getRequest.mockImplementation((url: string) => {
        if (url === '/api/v1/workflow/sequence') {
          return Promise.resolve({
            data: {
              note_max_chars: 1000,
              items: [{ status: 'todo', type: 'WP', note: '이전 시퀀스 멘트' }],
            },
          })
        }
        return Promise.resolve({ data: { providers: PROVIDERS, default_provider_id: 'aip_opus' } })
      })
      const wrapper = await mountDialog()

      expect((wrapper.get('[data-test="wpp-note"]').element as HTMLInputElement).value)
        .toBe('이전 시퀀스 멘트')
      expect(wrapper.find('[data-test="wpp-note-auto"]').exists()).toBe(true)
    })

    it('사용자가 먼저 입력하면 늦게 도착한 프리필이 그 값을 덮지 않는다', async () => {
      let release: (value: any) => void = () => {}
      getRequest.mockImplementation((url: string) => {
        if (url === '/api/v1/workflow/sequence') {
          return new Promise((resolve) => { release = resolve })
        }
        return Promise.resolve({ data: { providers: PROVIDERS, default_provider_id: 'aip_opus' } })
      })
      const wrapper = await mountDialog()
      await wrapper.get('[data-test="wpp-note"]').setValue('사용자가 먼저 적은 값')

      release({
        data: {
          note_max_chars: 1000,
          items: [{ status: 'todo', type: 'WP', note: '이전 시퀀스 멘트' }],
        },
      })
      await flushPromises()

      expect((wrapper.get('[data-test="wpp-note"]').element as HTMLInputElement).value)
        .toBe('사용자가 먼저 적은 값')
      expect(wrapper.find('[data-test="wpp-note-auto"]').exists()).toBe(false)
    })

    it('머리 행 note가 빈 문자열이면 칸도 비어 있고 안내도 뜨지 않는다', async () => {
      getRequest.mockImplementation((url: string) => {
        if (url === '/api/v1/workflow/sequence') {
          return Promise.resolve({
            data: { note_max_chars: 1000, items: [{ status: 'todo', type: 'WP', note: '' }] },
          })
        }
        return Promise.resolve({ data: { providers: PROVIDERS, default_provider_id: 'aip_opus' } })
      })
      const wrapper = await mountDialog()

      expect((wrapper.get('[data-test="wpp-note"]').element as HTMLInputElement).value).toBe('')
      expect(wrapper.find('[data-test="wpp-note-auto"]').exists()).toBe(false)
    })
  })
})

// 0405 T0011 rev2 — 반려: "AI공급자 선택할게 없으면 [2 후보공급자]는 안나오게 하고 1만
// 선택하고 생성할수 있게 해야하지 않겠니?" / "AI공급자 선택할게 없으면 [AI호출이 의미
// 없잖아] [+ 문서생성] 이 맨 우측으로 오게하고 이걸 강조해야지"
describe('WorkPlanProposalDialog — 고를 공급자가 하나도 없을 때', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
    localStorage.clear()
    postRequest.mockReset()
    getRequest.mockReset()
    getRequest.mockResolvedValue({ data: { providers: [], default_provider_id: null } })
  })

  /** 목록을 아직 한 번도 받지 않은 상태에서 여는 창. */
  function mountUnloaded() {
    const docTypeStore = useDocTypeStore()
    docTypeStore.items = TYPES as any
    docTypeStore.labelMap = Object.fromEntries(TYPES.map((item) => [item.code, item.label]))
    // 0429 T0004: this block seeds the store locally instead of calling the shared
    // seedStores() above — needs the same canonical-registry seed so [type index 0/2]
    // below still picks DS/T, not whatever order `items` happens to carry.
    docTypeStore.workPlanCountableTypes = TYPES_WP as any
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
    // flowgate.default.0423 T0005 item 11: checked types (DS, T) are left out of
    // quantities for the server to derive; only unchecked types force an explicit 0.
    expect(body.quantities).toEqual({ D: 0, TS: 0 })
    wrapper.unmount()
  })

  it('공급자가 없어도 다른 AI 실행 중에는 문서 생성을 잠근다', async () => {
    const wrapper = await mountNoProviders()
    await wrapper.setProps({ aiActive: true })
    await pick(wrapper, 'type', 0)
    expect(wrapper.get('[data-test="wpp-notice"]').text()).toContain('다른 AI 실행')
    expect(wrapper.get('[data-test="wpp-create-empty"]').attributes('disabled')).toBeDefined()
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

  it('공급자가 없어도 플래너 멘트 입력은 그대로 있고 [문서생성]에 실린다', async () => {
    postRequest.mockResolvedValue({
      data: { ok: true, doc_id: 'flowgate.default.0405.0011-WP', title: '작업계획', body: {} },
    })
    const wrapper = await mountNoProviders()
    expect(wrapper.find('[data-test="wpp-note"]').exists()).toBe(true)
    await pick(wrapper, 'type', 0)
    await wrapper.get('[data-test="wpp-note"]').setValue('공급자 없는 프로젝트의 지시')
    await wrapper.get('[data-test="wpp-create-empty"]').trigger('click')
    await flushPromises()

    const [, body] = postRequest.mock.calls[0]
    expect(body.defaults.note).toBe('공급자 없는 프로젝트의 지시')
    wrapper.unmount()
  })

  // flowgate.default.0416 TR0005 — 고를 공급자가 없으면 ② 칸과 함께 실행 프로바이더 박스도
  // 그리지 않는다(고를 것이 없다). 전달 멘트 입력은 그대로 남는다.
  it('실행 프로바이더 박스도 그리지 않는다', async () => {
    const wrapper = await mountNoProviders()
    expect(wrapper.find('[data-test="wpp-default-provider"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="wpp-note"]').exists()).toBe(true)
    wrapper.unmount()
  })
})

// flowgate.default.0421 T0005 작업 B-3 — 화면 문안이 [문서생성]을 AI 생성으로 오해시키지
// 않는지 확인한다. [문서생성]=AI 미호출 보존, [AI 호출]=전달 멘트를 참고한 새 작성이라는
// 구분을 이미 있는 intro 줄에서 읽을 수 있어야 한다(새 카드·새 섹션을 만들지 않는다).
describe('WorkPlanProposalDialog — 버튼 역할 문안', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
    localStorage.clear()
    postRequest.mockReset()
    getRequest.mockReset()
    getRequest.mockResolvedValue({ data: { providers: PROVIDERS, default_provider_id: 'aip_opus' } })
  })

  it('intro가 [문서생성]=AI 미호출 보존과 [AI 호출]=새 작성을 구분해 보여준다', async () => {
    const wrapper = await mountDialog()
    const intro = wrapper.get('[data-test="wpp-intro"]').text()
    expect(intro).toContain('AI를 부르지 않고')
    expect(intro).toContain('새로 작성')
  })
})
