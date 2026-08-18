import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import type { StepState } from '@main/workflow/workflowViewState'

// flowgate.default.0434 B0001 — "F5를 누르지 않으면 적용되지 않음".
//
// 사이드바의 [프로바이더 배정] 칸은 GET /documents/{id}/work-plan 을 문서를 열 때(docId /
// typeCode 가 바뀔 때) 한 번만 읽었다. 바로 옆 작업계획 표에서 공급자를 배정하거나 수량을
// 고쳐 [저장]을 눌러도 이 칸은 저장 전 숫자를 그대로 그렸고, 사람이 F5 를 눌러야 바뀌었다 —
// 화면상으로는 "배정이 적용되지 않는다". 실제 앱(격리 인스턴스 + 헤드리스 Chrome)에서
// 저장 직후 사이드바 "미지정 9단계" vs F5 후 "미지정 11단계" 로 관측된 그 결함이다.
//
// 서버는 계획 저장마다 document_explorer_refresh(operation='updated') 를 보내고,
// useFlowGateSse 가 그것을 fg:document_content_changed 창 이벤트로 바꿔 넣는다. 이 칸은
// 그 이벤트를 들어야 한다 — [질의 응답] 칸이 fg:qa_refresh 를 듣는 것과 같은 얼개이며,
// 그래서 내 저장이든 AI 워커의 채우기든 같은 경로로 다시 읽힌다.
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
  docId: 'p.none.0434.0002-WP',
  typeCode: 'WP' as string | null,
  reviewStatus: null as string | null,
  rejectReason: null,
  stepStates: [] as StepState[],
  nextStepIndex: null as number | null,
  collapsed: false,
}

/** GET /work-plan answer. Mutated between fetches to model a save that changed the plan. */
let plan = { assignment_summary: [] as unknown[], unassigned_step_count: 0 }

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  plan = { assignment_summary: [], unassigned_step_count: 6 }
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/work-plan')) return Promise.resolve({ data: plan })
    return Promise.resolve({ data: { qa: { items: [] } } })
  })
  postRequest.mockResolvedValue({ data: { ok: true } })
})

function mountPanel(docId: string, typeCode: string | null = 'WP') {
  return mount(DocInfoPanel, {
    props: { ...baseProps, docId, typeCode },
    global: { plugins: [i18n] },
  })
}

// fg:document_content_changed is a global window event and other suites can leave panels
// mounted, so every test uses its own docId and counts only that doc's own endpoint.
function planCallsFor(docId: string) {
  return getRequest.mock.calls.filter(
    (c) => c[0] === `/api/v1/documents/${encodeURIComponent(docId)}/work-plan`,
  ).length
}

const unassignedText = (n: number) =>
  i18n.global.t('main.doc_info_panel.wp_unassigned_steps', { n })

describe('DocInfoPanel work-plan assignment card — live refresh (0434 B0001)', () => {
  it('refetches and repaints on fg:document_content_changed for this doc (no F5)', async () => {
    const docId = 'wp.live.match.0434-WP'
    const wrapper = mountPanel(docId)
    await flushPromises()

    // Positive control: the card is really rendering the mount-time answer, so a later
    // "it changed" assertion cannot pass just because the card is missing.
    expect(planCallsFor(docId)).toBe(1)
    expect(wrapper.find('.dip-qa-error').text()).toBe(unassignedText(6))

    // A save just landed: one step got a provider, and the plan grew.
    plan = {
      assignment_summary: [{ provider_id: 'aip_x', display_name: 'haiku', step_count: 1 }],
      unassigned_step_count: 9,
    }
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: { doc_id: docId, revision_no: 1 },
    }))
    await flushPromises()

    expect(planCallsFor(docId)).toBe(2)
    expect(wrapper.find('.dip-qa-error').text()).toBe(unassignedText(9))
    expect(wrapper.find('.dip-wp-prov').text()).toBe('haiku')
    wrapper.unmount()
  })

  it('ignores the event for a different document', async () => {
    const docId = 'wp.live.ignore.0434-WP'
    const wrapper = mountPanel(docId)
    await flushPromises()
    expect(planCallsFor(docId)).toBe(1)

    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: { doc_id: 'some.other.doc-WP' },
    }))
    await flushPromises()

    expect(planCallsFor(docId)).toBe(1)
    wrapper.unmount()
  })

  it('removes the listener on unmount', async () => {
    const docId = 'wp.live.unmount.0434-WP'
    const wrapper = mountPanel(docId)
    await flushPromises()
    expect(planCallsFor(docId)).toBe(1)

    wrapper.unmount()
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: { doc_id: docId },
    }))
    await flushPromises()

    expect(planCallsFor(docId)).toBe(1)
  })

  it('stays silent for a non-WP document (the card does not exist there)', async () => {
    const docId = 'wp.live.nonwp.0434-T'
    const wrapper = mountPanel(docId, 'T')
    await flushPromises()
    expect(planCallsFor(docId)).toBe(0)

    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: { doc_id: docId },
    }))
    await flushPromises()

    // fetchWpAssignments returns early unless typeCode is WP, so no request is made.
    expect(planCallsFor(docId)).toBe(0)
    wrapper.unmount()
  })
})
