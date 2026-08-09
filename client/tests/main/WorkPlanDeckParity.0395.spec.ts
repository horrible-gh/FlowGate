// flowgate.default.0395 — 시안 xc32frrg 대조 회귀.
//
// 0032-M 이 반려한 것은 "시안대로 안 했다" 한 가지다. 그 시안이 그린 요소 가운데
// 컴포넌트 경계를 넘는 두 가지 — 액션바의 [연속 작업에 채우기](화면 1 하단 바)와
// 문서 정보의 [프로바이더 배정 (단계 기준)](화면 1 우측) — 를 여기서 못박는다.
// 두 화면 안쪽 요소는 WorkPlanCreateDialog / WorkPlanEditor 스펙이 맡는다.
import { config, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'

const { getRequest, postRequest, patchRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  patchRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
  patchRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const originalGlobalStubs = { ...config.global.stubs }

const WORK_PLAN_READ = {
  ok: true,
  doc_id: 'flowgate.default.0395.0003-WP',
  body: { wp_version: 1, counted_types: [], quantities: {}, provider_candidates: [], defaults: {}, steps: [] },
  assignment_summary: [
    { provider_id: 'aip_opus', display_name: 'Claude Opus', step_count: 4 },
    { provider_id: 'aip_codex', display_name: 'GPT (codex)', step_count: 3 },
  ],
  unassigned_step_count: 2,
}

beforeEach(() => {
  config.global.stubs = { ...originalGlobalStubs, teleport: true }
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/work-plan/applications')) return Promise.resolve({ data: { items: [], broken_lines: 0 } })
    if (url.includes('/work-plan')) return Promise.resolve({ data: WORK_PLAN_READ })
    return Promise.resolve({ data: { ok: true } })
  })
  postRequest.mockResolvedValue({ data: { ok: true } })
})

afterEach(() => {
  config.global.stubs = { ...originalGlobalStubs }
})

describe('시안 xc32frrg 대조 — 액션바', () => {
  const props = {
    docId: 'flowgate.default.0395.0003-WP',
    projectId: 'flowgate',
    groupId: 'flowgate.default.0395',
    docRef: 'flowgate.default.0395.0003-WP',
    reviewStatus: 'pending_review',
    mode: 'review' as const,
  }

  function fillButton(wrapper: ReturnType<typeof mount>) {
    return wrapper.findAll('button').find((b) => b.text().includes('연속 작업에 채우기'))
  }

  it('작업계획 문서의 액션바에 [연속 작업에 채우기]가 있고, 누르면 fill-continuous 를 낸다', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...props, docType: 'WP' },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    const button = fillButton(wrapper)
    expect(button).toBeDefined()
    await button!.trigger('click')
    expect(wrapper.emitted('fill-continuous')).toHaveLength(1)
  })

  it('작업계획이 아닌 문서에서는 그 단추가 없다', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...props, docType: 'TR' },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    expect(fillButton(wrapper)).toBeUndefined()
  })
})

describe('시안 xc32frrg 대조 — 문서 정보', () => {
  const props = {
    docId: 'flowgate.default.0395.0003-WP',
    typeCode: 'WP',
    groupId: 'flowgate.default.0395',
    projectId: 'flowgate',
    reviewStatus: 'pending_review',
    rejectReason: null,
    stepStates: [],
    nextStepIndex: null,
    collapsed: false,
  }

  it('작업계획 문서 정보에 [프로바이더 배정 (단계 기준)] 칸이 단계 수와 함께 뜬다', async () => {
    const wrapper = mount(DocInfoPanel, {
      props: props as never,
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await flushPromises()

    const rows = wrapper.findAll('.dip-wp-assignments li')
    expect(wrapper.text()).toContain('프로바이더 배정 (단계 기준)')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('Claude Opus')
    expect(rows[0].text()).toContain('4단계')
    expect(wrapper.text()).toContain('미지정 2단계')
  })

  it('작업계획이 아닌 문서에서는 그 칸이 없다', async () => {
    const wrapper = mount(DocInfoPanel, {
      props: { ...props, typeCode: 'TR' } as never,
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await flushPromises()
    expect(wrapper.find('.dip-wp-assignments').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('프로바이더 배정 (단계 기준)')
  })
})
