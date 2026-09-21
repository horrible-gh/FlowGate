// flowgate.default.0554 T0010 §9 / D0007 §6.3 — the two always-visible sidebar summary lines
// ("검수 설정된 단계" / "사전지시 작성된 단계") the approved deck t17hbdfg v8 draws next to the
// work-plan editor. Unlike the [프로바이더 배정] box above them, these render even when nothing
// is set — a short "없음" line rather than being hidden entirely.
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import type { StepState } from '@main/workflow/workflowViewState'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), head: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
}))

import DocInfoPanel from '@main/components/DocInfoPanel.vue'

const baseProps = {
  docId: 'flowgate.default.0554.0011-WP',
  typeCode: 'WP' as string | null,
  reviewStatus: null as string | null,
  rejectReason: null,
  stepStates: [] as StepState[],
  nextStepIndex: null as number | null,
  collapsed: false,
}

function mountPanel(planBody: any, typeCode: string | null = 'WP') {
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/work-plan')) {
      return Promise.resolve({ data: { assignment_summary: [], unassigned_step_count: 0, body: planBody } })
    }
    return Promise.resolve({ data: { qa: { items: [] } } })
  })
  return mount(DocInfoPanel, { props: { ...baseProps, typeCode }, global: { plugins: [i18n] } })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  postRequest.mockResolvedValue({ data: { ok: true } })
})

const STEPS = [
  { key: 'D#1', locked: false, pair_role: 'single', review_count: 0, pre_instruction_text: null, pre_instruction_attachment: null },
  { key: 'T#1', locked: false, pair_role: 'instruction', review_count: 2, pre_instruction_text: '지시문', pre_instruction_attachment: null },
  { key: 'TR#1', locked: false, pair_role: 'result', review_count: 1, pre_instruction_text: null, pre_instruction_attachment: null },
  { key: 'TS#1', locked: false, pair_role: 'instruction', review_count: 0, pre_instruction_text: null, pre_instruction_attachment: { doc_id: 'x' } },
  { key: 'TSR#1', locked: true, pair_role: 'result', review_count: 0, pre_instruction_text: null, pre_instruction_attachment: null },
]

describe('DocInfoPanel — 검수/사전지시 요약 (0554 T0010 §9)', () => {
  it('검수·사전지시가 설정된 단계 수와 목록을 보여준다 (잠긴 단계는 분모·목록 모두에서 제외)', async () => {
    const wrapper = mountPanel({ steps: STEPS })
    await flushPromises()

    // 4 unlocked steps total, 2 with review_count != 0 (T#1, TR#1).
    expect(wrapper.get('[data-test="wp-review-summary"]').text())
      .toBe(i18n.global.t('main.doc_info_panel.wp_review_summary_text', { total: 4, n: 2, list: 'T#1 · TR#1' }))

    // Instruction-eligible = non-result, non-locked = D#1, T#1, TS#1 (3). Written = T#1, TS#1 (2).
    expect(wrapper.get('[data-test="wp-instruction-summary"]').text())
      .toBe(i18n.global.t('main.doc_info_panel.wp_instruction_summary_text', { total: 3, n: 2, list: 'T#1 · TS#1' }))
  })

  it('아무것도 설정되지 않으면 짧은 없음 상태만 보인다', async () => {
    const empty = STEPS.map((s) => ({ ...s, review_count: 0, pre_instruction_text: null, pre_instruction_attachment: null }))
    const wrapper = mountPanel({ steps: empty })
    await flushPromises()

    expect(wrapper.get('[data-test="wp-review-summary"]').text())
      .toBe(i18n.global.t('main.doc_info_panel.wp_review_summary_none'))
    expect(wrapper.get('[data-test="wp-instruction-summary"]').text())
      .toBe(i18n.global.t('main.doc_info_panel.wp_instruction_summary_none'))
  })

  it('WP가 아닌 문서에서는 두 요약 섹션이 아예 렌더되지 않는다', async () => {
    const wrapper = mountPanel({ steps: STEPS }, 'T')
    await flushPromises()

    expect(wrapper.find('[data-test="wp-review-summary"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="wp-instruction-summary"]').exists()).toBe(false)
  })
})
