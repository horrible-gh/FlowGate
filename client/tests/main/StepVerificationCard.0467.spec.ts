// flowgate.default.0467 R0001/T0002 — [단계별 확인] accordion card.
//
// Pins the same accordion idiom AttachmentCard already uses (collapsed by default, the
// whole title row toggles), plus the three read states this card can be in: no section at
// all (older TR / non-TR), explicitly registered as none, and a real list of sections each
// collapsed on their own.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import StepVerificationCard from '@main/components/StepVerificationCard.vue'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  deleteRequest: vi.fn(),
  postFormRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

const DOC_ID = 'flowgate.default.0467.0002-TR'

function apiResponse(data: unknown) {
  return { data: { data } }
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
})

describe('[단계별 확인] 카드', () => {
  it('마운트 시 접힌 상태로 시작한다(D0010 6-3과 같은 규칙)', async () => {
    getRequest.mockResolvedValue(apiResponse({ found: false, declared_none: false, sections: [] }))
    const wrapper = mount(StepVerificationCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
    await flushPromises()

    expect(wrapper.find('.step-verify-card').classes()).toContain('collapsed')
  })

  it('없음으로 등록된 문서는 그 사실을 보여준다', async () => {
    getRequest.mockResolvedValue(apiResponse({ found: true, declared_none: true, sections: [] }))
    const wrapper = mount(StepVerificationCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
    await flushPromises()

    expect(wrapper.find('.step-verify-empty-note').text()).toBe('확인할 것이 없다고 등록되었습니다.')
  })

  it('섹션 목록을 렌더링하고, 각 섹션은 자기 자신도 접힌 아코디언으로 시작한다', async () => {
    getRequest.mockResolvedValue(apiResponse({
      found: true,
      declared_none: false,
      sections: [
        {
          title: '로그인 확인',
          summary: '잘못된 비밀번호면 오류가 뜬다',
          steps: [
            { description: '아무 비밀번호로 로그인 시도', expectations: ['오류 문구가 뜬다', '로그인은 되지 않는다'] },
          ],
        },
        {
          title: '두 번째 확인',
          summary: '두 번째 요약',
          steps: [{ description: '스텝 2', expectations: ['기대치 2'] }],
        },
      ],
    }))
    const wrapper = mount(StepVerificationCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
    await flushPromises()

    const sections = wrapper.findAll('.step-verify-section')
    expect(sections).toHaveLength(2)
    sections.forEach((section) => expect(section.classes()).toContain('collapsed'))
    expect(sections[0].find('.step-verify-section-title').text()).toBe('로그인 확인')
    expect(sections[0].find('.step-verify-section-summary').text()).toBe('잘못된 비밀번호면 오류가 뜬다')

    await sections[0].find('.step-verify-section-hd').trigger('click')
    expect(wrapper.findAll('.step-verify-section')[0].classes()).not.toContain('collapsed')
    const expectations = wrapper.findAll('.step-verify-step')[0].findAll('.step-verify-expectations li')
    expect(expectations.map((e) => e.text())).toEqual(['오류 문구가 뜬다', '로그인은 되지 않는다'])
  })

  it('접힌 상태에서도 첫 섹션 제목이 요약 줄에 드러난다', async () => {
    getRequest.mockResolvedValue(apiResponse({
      found: true,
      declared_none: false,
      sections: [
        { title: '첫 섹션', summary: '요약', steps: [] },
        { title: '둘째', summary: '요약2', steps: [] },
      ],
    }))
    const wrapper = mount(StepVerificationCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
    await flushPromises()

    expect(wrapper.find('.step-verify-fold-summary').text()).toBe('· 첫 섹션 외 1개')
    expect(wrapper.find('.step-verify-count-pill').text()).toBe('2개')
  })

  it('문서를 바꾸면 새 문서 기준으로 다시 불러온다', async () => {
    getRequest.mockResolvedValue(apiResponse({ found: false, declared_none: false, sections: [] }))
    const wrapper = mount(StepVerificationCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(
      `/api/v1/documents/${encodeURIComponent(DOC_ID)}/step-verification`,
    )

    getRequest.mockClear()
    await wrapper.setProps({ docId: 'flowgate.default.0467.0009-TR' })
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(
      '/api/v1/documents/flowgate.default.0467.0009-TR/step-verification',
    )
  })
})

// flowgate.default.0467 T0012 — D0005 §3/§6 상태 E(절 누락/형식 오류)·상태 F(조회 실패)·전환
// 상태(로딩)·재조회 경합 규칙. 위 5개는 승인된 시안 opg4tsar의 상태 A~D 회귀만 지킨다;
// 아래는 D0005가 재반려를 거쳐 확정한, opg4tsar에는 없던 계약들을 각각 독립적으로 고정한다 —
// 상태 E는 `found: false`(절 헤딩 자체 없음)·`found: true, sections: []`(형식 오류) 두 서버
// 응답 분기를 모두 다룬다.
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => { resolve = res })
  return { promise, resolve }
}

describe('[단계별 확인] 카드 — 로딩·절 누락/형식 오류·조회 실패·재조회 경합 (D0005 §3/§6)', () => {
  it('조회가 시작된 직후부터 응답 전까지는 배지 없이 "불러오는 중" 문구만 보인다', async () => {
    const { promise } = deferred<any>()
    getRequest.mockReturnValue(promise)
    const wrapper = mount(StepVerificationCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
    await Promise.resolve()

    expect(wrapper.find('.step-verify-fold-summary').text()).toBe('불러오는 중')
    expect(wrapper.find('.step-verify-count-pill').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('확인할 것이 없다고 등록되었습니다')
  })

  it('섹션 자체가 없는 문서(구버전 TR 등)도 "없음"(상태 D)이 아니라 절 누락/형식 오류 문구를 보여준다', async () => {
    getRequest.mockResolvedValue(apiResponse({ found: false, declared_none: false, sections: [] }))
    const wrapper = mount(StepVerificationCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
    await flushPromises()

    expect(wrapper.text()).toContain('이 문서에는 단계별 확인 절이 없거나 비어 있습니다')
    expect(wrapper.text()).not.toContain('확인할 것이 없다고 등록되었습니다')
    expect(wrapper.find('.step-verify-fold-summary').text()).toBe('· 확인 절이 등록되지 않음')
    expect(wrapper.find('.step-verify-count-pill').exists()).toBe(false)
  })

  it('절 헤딩은 있지만 하위 섹션도 명시적 "없음"도 없으면, "없음"(상태 D)과 다른 절 누락/형식 오류 문구를 보여준다', async () => {
    getRequest.mockResolvedValue(apiResponse({ found: true, declared_none: false, sections: [] }))
    const wrapper = mount(StepVerificationCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
    await flushPromises()

    expect(wrapper.text()).toContain('이 문서에는 단계별 확인 절이 없거나 비어 있습니다')
    expect(wrapper.text()).not.toContain('확인할 것이 없다고 등록되었습니다')
    expect(wrapper.find('.step-verify-fold-summary').text()).toBe('· 확인 절이 등록되지 않음')
    expect(wrapper.find('.step-verify-count-pill').exists()).toBe(false)
  })

  it('조회 자체가 실패하면, "없음"·절 누락 문구와 모두 다른 전용 오류 문구를 보여준다', async () => {
    getRequest.mockRejectedValue(new Error('network down'))
    const wrapper = mount(StepVerificationCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
    await flushPromises()

    expect(wrapper.text()).toContain('확인 정보를 불러오지 못했습니다. 다시 시도해 주세요')
    expect(wrapper.find('.step-verify-fold-summary').text()).toBe('· 확인 정보를 불러오지 못함')
    expect(wrapper.find('.step-verify-count-pill').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('확인할 것이 없다고 등록되었습니다')
    expect(wrapper.text()).not.toContain('단계별 확인 절이 없거나 비어 있습니다')
  })

  it('나중에 시작한 조회만 반영되고, 먼저 시작된 조회의 늦은 응답은 버려진다', async () => {
    const first = deferred<any>()
    const second = deferred<any>()
    getRequest.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const wrapper = mount(StepVerificationCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
    await Promise.resolve()
    ;(wrapper.vm as any).fetchData()
    await Promise.resolve()

    second.resolve(apiResponse({ found: true, declared_none: true, sections: [] }))
    await flushPromises()
    expect(wrapper.text()).toContain('확인할 것이 없다고 등록되었습니다')

    first.resolve(apiResponse({
      found: true,
      declared_none: false,
      sections: [{ title: '먼저 시작된 낡은 응답', summary: '', steps: [] }],
    }))
    await flushPromises()

    expect(wrapper.text()).not.toContain('먼저 시작된 낡은 응답')
    expect(wrapper.text()).toContain('확인할 것이 없다고 등록되었습니다')
  })
})
