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

  it('섹션이 없는 문서(구버전 TR 등)는 "해당 없음" 문구를 보여준다', async () => {
    getRequest.mockResolvedValue(apiResponse({ found: false, declared_none: false, sections: [] }))
    const wrapper = mount(StepVerificationCard, { props: { docId: DOC_ID }, global: { plugins: [i18n] } })
    await flushPromises()

    expect(wrapper.find('.step-verify-empty-note').text()).toBe('이 문서에는 단계별 확인 섹션이 없습니다.')
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
