// flowgate.default.0434 TR0005 rev4 — B0001 재반려("F5를 누르지 않으면 적용되지 않음").
//
// 반려자가 실제로 있던 화면: WP(작업계획) 탭을 열어 둔 채 그 문서를 반려하면 그룹 워커가
// AI 실행으로 계획을 다시 써서 새 리비전을 등록한다. dev 8080 기록(2026-08-18 10:53:26 시작 →
// 10:58:21 등록 → 10:58:49 새로고침)이 그 순서를 그대로 남겼다. 그런데 이 편집기는 *자기가
// 시작한* AI 채우기(startAiFill)의 run_id 만 보는 onAiInvoke 밖에 없어서, 워커가 등록한
// 새 계획은 F5 전까지 화면에 오지 않았다.
//
// 서버는 이미 저장마다 document_explorer_refresh(operation='updated')를 보내고
// useFlowGateSse 가 그것을 fg:document_content_changed 로 바꿔 넣는다. 이 시험은 그 이벤트
// 하나로 편집기가 다시 읽고 **화면 글자까지** 바뀌는지, 그리고 저장하지 않은 편집을 덮어쓰지
// 않는지를 고정한다.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import WorkPlanEditor from '@main/components/WorkPlanEditor.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest, putRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  getRequest,
  postRequest,
  putRequest,
  patchRequest: vi.fn(),
}))

const DOC_ID = 'flowgate.default.0434.0002-WP'
const OTHER_DOC_ID = 'flowgate.default.0434.0003-WP'
const GROUP = 'flowgate.default.0434'

const TYPES = [
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 2 },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false, sort_order: 3 },
]

const REGISTERED_PROVIDERS = [
  { id: 'aip_opus', name: 'Claude Opus', group_label: 'Claude · CLI' },
]

function planResponse(over: Record<string, unknown>) {
  return {
    ok: true,
    doc_id: DOC_ID,
    doc_type: 'WP',
    title: '0434 작업계획',
    group_id: GROUP,
    parent_doc_id: `${GROUP}.0001-R`,
    status: 'open',
    doc_review_status: 'pending_review',
    origin: 'human',
    registered_providers: REGISTERED_PROVIDERS,
    provider_status: [],
    assignment_summary: [],
    last_application: null,
    editable: true,
    edit_locked_reason: null,
    ...over,
  }
}

/** 반려 직전 사람이 보고 있던 계획 — 설계 1장, 단계 1건. */
const BEFORE_WORKER = planResponse({
  revision_no: 18,
  unassigned_step_count: 1,
  totals: { design_sheets: 1, work_sets: 0, steps: 1 },
  body: {
    wp_version: 1,
    binding: 'advisory',
    counted_types: ['D', 'T'],
    quantities: { D: { unit: 'sheet', count: 1 }, T: { unit: 'set', count: 0 } },
    provider_candidates: [],
    defaults: { provider_id: null, note: '' },
    steps: [
      { key: 'D#1', type: 'D', ordinal: 1, pair_key: null, pair_role: 'single', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'human' },
    ],
  },
})

/** 워커가 등록한 새 리비전 — 설계 0장, 작업 1세트(T/TR 2단계). */
const AFTER_WORKER = planResponse({
  revision_no: 19,
  unassigned_step_count: 2,
  totals: { design_sheets: 0, work_sets: 1, steps: 2 },
  body: {
    wp_version: 1,
    binding: 'advisory',
    counted_types: ['D', 'T'],
    quantities: { D: { unit: 'sheet', count: 0 }, T: { unit: 'set', count: 1 } },
    provider_candidates: [],
    defaults: { provider_id: null, note: '' },
    steps: [
      { key: 'T#1', type: 'T', ordinal: 1, pair_key: 'TR#1', pair_role: 'instruction', provider_id: null, provider_display_name: null, note: '워커가 채운 지시', locked: false, locked_reason: null, origin: 'ai' },
      { key: 'TR#1', type: 'TR', ordinal: 1, pair_key: 'T#1', pair_role: 'result', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'ai' },
    ],
  },
})

let planPayload: Record<string, unknown> = BEFORE_WORKER
let planReads = 0

// VTU 는 시험이 끝나도 마운트를 자동으로 걷지 않는다. 이 시험은 window 이벤트로 동작을
// 확인하므로, 앞 시험의 살아 있는 인스턴스가 같은 이벤트를 함께 받으면 요청 수가 오염된다.
const mounted: ReturnType<typeof mount>[] = []

function mountEditor(props: Record<string, unknown> = {}) {
  const wrapper = mount(WorkPlanEditor, {
    props: { docId: DOC_ID, projectId: 'flowgate', ...props },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
  mounted.push(wrapper)
  return wrapper
}

function contentChanged(docId: string) {
  window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
    detail: { doc_id: docId, operation: 'updated' },
  }))
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  useProjectStore().currentProjectId = 'flowgate'
  planPayload = BEFORE_WORKER
  planReads = 0
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/document-types')) return Promise.resolve({ data: { data: TYPES } })
    if (url.includes('/ai-invoke/leases')) return Promise.resolve({ data: { items: [] } })
    if (url.includes('/ai-invoke/providers')) {
      return Promise.resolve({ data: { providers: structuredClone(REGISTERED_PROVIDERS), default_provider_id: 'aip_opus' } })
    }
    if (url.includes('/work-plan')) {
      planReads += 1
      return Promise.resolve({ data: structuredClone(planPayload) })
    }
    return Promise.reject(new Error(`unexpected url: ${url}`))
  })
})

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
})

describe('WorkPlanEditor — 워커가 등록한 계획이 F5 없이 보인다 (0434 B0001)', () => {
  it('이 문서의 fg:document_content_changed 로 다시 읽고 화면 글자까지 바뀐다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    // 대조군: 이벤트 전에는 반려 직전 계획이 실제로 그려져 있다. 이걸 먼저 단언해야
    // "바뀌었다"가 빈 화면 때문에 저절로 통과하는 일이 없다.
    const notes = () => wrapper.findAll('.wp-step-msg')
      .map((input) => (input.element as HTMLInputElement).value)

    expect(planReads).toBe(1)
    expect(wrapper.findAll('.wp-step-row')).toHaveLength(1)
    expect(wrapper.get('.wp-step-list').text()).toContain('기본설계')
    expect(notes()).not.toContain('워커가 채운 지시')
    expect(wrapper.findAll('.wp-sum-value').map((n) => n.text())).toContain('1단계')

    planPayload = AFTER_WORKER
    contentChanged(DOC_ID)
    await flushPromises()

    expect(planReads).toBe(2)
    expect(wrapper.findAll('.wp-step-row')).toHaveLength(2)
    expect(wrapper.get('.wp-step-list').text()).toContain('작업지시')
    expect(notes()).toContain('워커가 채운 지시')
    expect(wrapper.findAll('.wp-sum-value').map((n) => n.text())).toContain('2단계')
  })

  it('다른 문서의 이벤트는 무시한다', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    expect(planReads).toBe(1)

    planPayload = AFTER_WORKER
    contentChanged(OTHER_DOC_ID)
    await flushPromises()

    expect(planReads).toBe(1)
    expect(wrapper.findAll('.wp-step-row')).toHaveLength(1)
  })

  it('저장하지 않은 표 편집이 있으면 다시 읽지 않는다 — 남의 저장이 내 입력을 덮지 않는다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    // 수량을 하나 올려 저장 전 상태(dirty)로 만든다.
    const steppers = wrapper.findAll('.wp-stepper-btn')
    await steppers[steppers.length - 1].trigger('click')
    await flushPromises()
    const dirtyRows = wrapper.findAll('.wp-step-row').length
    expect(dirtyRows).toBeGreaterThan(1)

    planPayload = AFTER_WORKER
    contentChanged(DOC_ID)
    await flushPromises()

    expect(planReads).toBe(1)
    expect(wrapper.findAll('.wp-step-row')).toHaveLength(dirtyRows)
  })

  // 실제 순서에서는 워커의 등록이 *AI 실행이 아직 도는 동안* 들어온다(10:58:21 등록,
  // 10:58:39 종료). 그동안 이 화면은 읽기 전용으로 잠기는데, 잠금은 편집 컨트롤을 막을 뿐
  // 새 리비전을 그리는 것까지 막아서는 안 된다.
  it('AI 실행으로 잠긴 동안 워커가 등록해도 즉시 그린다', async () => {
    const wrapper = mountEditor({ readOnly: true })
    await flushPromises()
    expect(wrapper.findAll('.wp-step-row')).toHaveLength(1)

    planPayload = AFTER_WORKER
    contentChanged(DOC_ID)
    await flushPromises()

    expect(planReads).toBe(2)
    expect(wrapper.findAll('.wp-step-row')).toHaveLength(2)
  })

  it('언마운트하면 리스너가 사라진다', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    expect(planReads).toBe(1)

    wrapper.unmount()
    planPayload = AFTER_WORKER
    contentChanged(DOC_ID)
    await flushPromises()

    expect(planReads).toBe(1)
  })
})
