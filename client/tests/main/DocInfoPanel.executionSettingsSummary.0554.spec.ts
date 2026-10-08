// flowgate.default.0554 T0010 §9 / D0007 §6.3 drew two always-visible sidebar summary lines
// ("검수 설정 요약" / "사전지시 작성 현황") next to the work-plan editor.
// flowgate.default.0649 T#3 (NR0003 §9) removes exactly those two lines. What stays: the
// [프로바이더 배정 (단계 기준)] box fed by the same GET, and — in the editor, not here — the
// per-step review pill and pre-instruction drawer.
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

function mountPanel(
  planBody: any,
  typeCode: string | null = 'WP',
  assignments: Array<{ provider_id: string; display_name: string; step_count: number }> = [],
  unassigned = 0,
) {
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/work-plan')) {
      return Promise.resolve({
        data: { assignment_summary: assignments, unassigned_step_count: unassigned, body: planBody },
      })
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

describe('DocInfoPanel — 검수/사전지시 요약 제거 (0649 T#3, NR0003 §9)', () => {
  it('검수·사전지시가 설정된 WP에서도 두 요약 섹션은 렌더되지 않는다', async () => {
    const wrapper = mountPanel({ steps: STEPS })
    await flushPromises()

    expect(wrapper.find('[data-test="wp-review-summary"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="wp-instruction-summary"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('검수 설정 요약')
    expect(wrapper.text()).not.toContain('사전지시 작성 현황')
  })

  it('요약 문구 i18n 키도 ko/en/ja 에서 사라졌다', () => {
    for (const locale of ['ko', 'en', 'ja'] as const) {
      const messages = i18n.global.getLocaleMessage(locale) as any
      const panel = messages.main.doc_info_panel
      for (const key of [
        'wp_review_summary_title', 'wp_review_summary_text', 'wp_review_summary_none',
        'wp_instruction_summary_title', 'wp_instruction_summary_text', 'wp_instruction_summary_none',
      ]) {
        expect(panel[key], `${locale}.${key}`).toBeUndefined()
      }
      // the kept box's copy is still there
      expect(typeof panel.wp_assignments).toBe('string')
    }
  })

  it('유지 대상: 프로바이더 배정(단계 기준) 섹션은 그대로 보인다', async () => {
    const wrapper = mountPanel(
      { steps: STEPS },
      'WP',
      [{ provider_id: 'p1', display_name: 'Provider One', step_count: 3 }],
      1,
    )
    await flushPromises()

    expect(wrapper.text()).toContain(i18n.global.t('main.doc_info_panel.wp_assignments'))
    expect(wrapper.find('.dip-wp-assignments').text()).toContain('Provider One')
    expect(wrapper.text()).toContain(i18n.global.t('main.doc_info_panel.wp_unassigned_steps', { n: 1 }))
  })

  it('WP가 아닌 문서에서도 요약 섹션은 없다', async () => {
    const wrapper = mountPanel({ steps: STEPS }, 'T')
    await flushPromises()

    expect(wrapper.find('[data-test="wp-review-summary"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="wp-instruction-summary"]').exists()).toBe(false)
  })
})
