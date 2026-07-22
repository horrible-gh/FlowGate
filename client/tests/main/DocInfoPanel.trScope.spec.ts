import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'
import type { TrScopeVerdict } from '@main/types/trScope'

// TR 작업범위 검증 결과 영역 (0299 D0004 §6). 이 영역은 0300 B0001 에서 타입 오류로
// 배포가 롤백된 자리다 — 신고/감지 목록 루프가 인라인 배열 리터럴을 써서 키가 string
// 으로 추론됐다. 렌더 결과를 여기서 묶어 두면 같은 자리를 다시 건드릴 때 vitest 로도
// 걸린다 (타입 쪽은 `npm run typecheck` 가 본다).

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

function mountPanel(trScope: TrScopeVerdict | null) {
  return mount(DocInfoPanel, {
    props: {
      docId: 'flowgate.default.0300.0005-TR',
      typeCode: 'TR',
      reviewStatus: null,
      rejectReason: null,
      trScope,
      stepStates: [],
      nextStepIndex: null,
      collapsed: false,
    },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  getRequest.mockResolvedValue({ data: { qa: { items: [] } } })
})

describe('DocInfoPanel TR 작업범위 검증 영역', () => {
  it('트 작업범위 결과가 없으면 영역 자체를 그리지 않는다', async () => {
    const wrapper = mountPanel(null)
    await flushPromises()
    expect(wrapper.find('.dip-trs-head').exists()).toBe(false)
  })

  it('신고/감지 목록을 판정·사유와 함께 그린다', async () => {
    const wrapper = mountPanel({
      verdict: 'warn',
      stage: 'warn',
      codes: ['TRV-004'],
      branch: 'flowgate_default_0300',
      reported: { count: 1, items: ['client/src/main/components/DocHeader.vue'] },
      detected: { count: 2, items: ['client/src/main/components/DocHeader.vue', 'client/package.json'] },
      unreported: { count: 1, items: ['client/package.json'] },
    })
    await flushPromises()
    const text = wrapper.text()

    expect(text).toContain('Warning')
    expect(text).toContain('flowgate_default_0300')
    // 어긋남 목록이 먼저, 신고/감지 전체 목록이 그다음
    expect(wrapper.find('.dip-trs-list.dip-trs-mismatch').exists()).toBe(true)
    expect(text).toContain('Reported files (1)')
    expect(text).toContain('Detected files (2)')
    expect(text).toContain('client/src/main/components/DocHeader.vue')
    expect(text).toContain('client/package.json')
    // 사유 코드는 사람이 읽는 문장으로 풀어 준다 (키가 그대로 새지 않는다)
    expect(text).toContain('The work folder has changes that the report does not list.')
    expect(text).not.toContain('main.doc_info_panel.')
  })

  it('신고/감지 슬라이스가 아예 없어도 0건으로 그린다', async () => {
    const wrapper = mountPanel({ verdict: 'pass', stage: 'observe' })
    await flushPromises()
    const text = wrapper.text()

    expect(text).toContain('Reported files (0)')
    expect(text).toContain('Detected files (0)')
    // 빈 목록에는 "(none)" 안내만 보인다 (테스트 로케일은 en)
    expect(wrapper.findAll('.dip-trs-more').length).toBe(2)
    expect(text).toContain('(none)')
  })

  it('items 가 count 보다 적으면 나머지를 "and n more" 로 알린다', async () => {
    const wrapper = mountPanel({
      verdict: 'pass',
      detected: { count: 12, items: ['a.py', 'b.py'] },
    })
    await flushPromises()
    const text = wrapper.text()

    expect(text).toContain('Detected files (12)')
    expect(text).toContain('and 10 more')
  })
})
